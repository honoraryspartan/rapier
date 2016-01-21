#!/usr/bin/env python 

import yaml, sys, getopt, itertools
from collections import OrderedDict

class PresortedList(list):
    def sort(self, *args, **kwargs):
        pass

class PresortedOrderedDict(OrderedDict):
    def items(self, *args, **kwargs):
        return PresortedList(OrderedDict.items(self, *args, **kwargs))

def ordered_load(stream, Loader=yaml.Loader, object_pairs_hook=PresortedOrderedDict):
    class OrderedLoader(Loader):
        pass
    def construct_mapping(loader, node):
        loader.flatten_mapping(node)
        return object_pairs_hook(loader.construct_pairs(node))
    OrderedLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_mapping)
    return yaml.load(stream, OrderedLoader)

class SwaggerGenerator(object):

    def __init__(self):
        pass

    def set_rapier_spec_from_filename(self, filename):
        self.filename = filename
        with open(filename) as f:
            self.rapier_spec = ordered_load(f.read(), yaml.SafeLoader)
            
    def set_opts(self, opts):
        self.opts = opts
        self.opts_keys = [k for k,v in opts]
        self.yaml_merge = '--yaml-merge' in self.opts_keys or '-m' in self.opts_keys
        self.include_impl = '--include-impl' in self.opts_keys or '-i' in self.opts_keys
        self.suppress_annotations = '--suppress-annotations' in self.opts_keys or '-s' in self.opts_keys

    def swagger_from_rapier(self, filename= None):
        if filename:
            self.set_rapier_spec_from_filename(filename)
        spec = self.rapier_spec 
        self.conventions = spec['conventions'] if 'conventions' in spec else {}     
        if 'selector_location' in self.conventions:
            if self.conventions['selector_location'] not in ['path-segment', 'path-parameter']:
                sys.exit('error: invalid value for selector_location: %s' % self.selector_location)
            self.discriminator_separator = '/' if self.conventions['selector_location'] == 'path-segment' else ';'
        else:
            self.discriminator_separator = ';'
        patterns = spec.get('patterns')
        self.swagger = PresortedOrderedDict()
        self.swagger['swagger'] = '2.0'
        self.swagger['info'] = dict()
        self.swagger_paths = PresortedOrderedDict()
        self.swagger_uris = PresortedOrderedDict()
        if 'consumes' in spec:
            self.swagger['consumes'] = as_list(spec.get('consumes'))
        else:
            self.swagger['consumes'] = ['application/json']
        if 'produces' in spec:
            self.swagger['produces'] = as_list(spec.get('produces'))
        else:
            self.swagger['produces'] = ['application/json']
        if 'securityDefinitions' in spec:
            self.swagger['securityDefinitions'] = spec['securityDefinitions']            
        if 'security' in spec:
            self.swagger['security'] = spec['security']            
        self.definitions = PresortedOrderedDict()
        self.patch_consumes = as_list(self.conventions['patch_consumes']) if 'patch_consumes' in self.conventions else ['application/merge-patch+json', 'application/json-patch+json']
        self.swagger['definitions'] = self.definitions
        self.swagger['paths'] = self.swagger_paths
        self.swagger['x-uris'] = self.swagger_uris
        self.header_parameters = self.build_standard_header_parameters()
        self.swagger['parameters'] = self.header_parameters
        self.swagger['responses'] = dict()
        self.swagger['info']['title'] = spec['title'] if 'title' in spec else 'untitled'
        self.swagger['info']['version'] = spec['version'] if 'version' in spec else 'initial'

        if 'entities' in spec:
            entities = spec['entities']
            self.uri_map = {'#/entities/%s' % name: entity for name, entity in entities.iteritems()}
            self.swagger_uri_map = {'#/entities/%s' % name: '#/definitions/%s' % name for name in entities.iterkeys()}
            self.uri_map.update({entity['id'] if 'id' in entity else '#%s' % name: entity for name, entity in entities.iteritems()})
            self.swagger_uri_map.update({entity['id'] if 'id' in entity else '#%s' % name: '#/definitions/%s' % name for name, entity in entities.iteritems()})
            self.swagger['definitions'] = self.definitions
            for entity_name, entity_spec in entities.iteritems():
                entity_spec['name'] = entity_name
            if 'error_response' in self.conventions:
                self.definitions['ErrorResponse'] = self.conventions['error_response']
                self.swagger_uri_map['#ErrorResponse'] = '#/definitions/ErrorResponse'
                self.error_response = self.global_definition_ref('#ErrorResponse')
            else:
                self.error_response = {}
            self.responses = self.build_standard_responses()
            self.response_sets = self.build_standard_response_sets()
            self.methods = self.build_standard_methods()
            for entity_name, entity_spec in entities.iteritems():
                definition = PresortedOrderedDict()
                if 'allOf' in entity_spec:
                    definition['allOf'] = [{key: self.swagger_uri_map[value] for key, value in ref.iteritems()} for ref in entity_spec['allOf']]
                if 'oneOf' in entity_spec:
                    definition['x-oneOf'] = [{key: self.swagger_uri_map[value] for key, value in ref.iteritems()} for ref in entity_spec['oneOf']]
                if  not 'type' in entity_spec or entity_spec['type'] == 'object': # TODO: maybe need to climb allOf tree to check this more fully
                    if 'properties' in entity_spec:
                        immutable_entity = entity_spec.get('readOnly', False)
                        properties = {prop_name: prop for prop_name, prop in entity_spec['properties'].iteritems() if not prop.get('implementation_private', False)}
                        if immutable_entity:
                            for prop in properties.itervalues():
                                prop['readOnly'] = True
                        definition['properties'] = properties
                    if 'required' in entity_spec:
                        definition['required'] = entity_spec['required']
                    if 'type' in entity_spec:
                        definition['type'] = entity_spec['type']
                self.definitions[entity_name] = definition
            for entity_name, entity_spec in entities.iteritems():
                if 'well_known_URLs' in entity_spec:
                    for well_known_URL in as_list(entity_spec['well_known_URLs']):
                        self.swagger['paths'][well_known_URL] = self.build_entity_interface(WellKnownURLSpec(well_known_URL, '#%s' % entity_name))
                rel_property_specs = self.get_relationship_property_specs('#%s' % entity_name, entity_spec)
                if len(rel_property_specs) > 0:
                    definition = self.definitions[entity_name]
                    if 'type' in entity_spec:
                        definition['type'] = entity_spec['type']
                if self.include_impl and 'implementation' in entity_spec:
                    implementation_spec_spec = ImplementationPathSpec(self.conventions, entity_spec['implementation'], '#%s' % entity_name)
                    implementation_spec_specs = [ImplementationPathSpec(self.conventions, e_s['implementation'], e_n) for e_n, e_s in entities.iteritems() if e_s.get('implementation') and e_s['implementation']['path'] == entity_spec['implementation']['path']]
                    entity_interface =  self.build_entity_interface(implementation_spec_spec, None, None, implementation_spec_specs)
                    self.swagger_paths[implementation_spec_spec.path_segment()] = entity_interface
                elif not self.include_impl and not entity_spec.get('abstract', False) and entity_spec.get('resource', True): 
                    entity_url_property_spec = EntityURLSpec('#%s' % entity_name, self)
                    self.swagger['x-uris'][entity_url_property_spec.path_segment()] = self.build_entity_interface(entity_url_property_spec)
                if 'query_paths' in entity_spec:
                    query_paths = [QueryPath(query_path, self) for query_path in as_list(entity_spec['query_paths'])]
                    for rel_property_spec in rel_property_specs:
                        rel_property_spec_stack = [rel_property_spec]
                        if self.include_impl and 'implementation' in entity_spec:
                            implementation_spec_spec = ImplementationPathSpec(self.conventions, entity_spec['implementation'], entity_name)
                            self.add_query_paths(query_paths[:], implementation_spec_spec, rel_property_spec_stack, rel_property_specs)
                        if 'well_known_URLs' in entity_spec:
                            well_known_URLs = as_list(entity_spec['well_known_URLs'])
                            leftover_query_paths = query_paths
                            for well_known_URL in well_known_URLs:
                                leftover_query_paths = query_paths[:]
                                baseURL_spec = WellKnownURLSpec(well_known_URL, entity_name)
                                self.add_query_paths(leftover_query_paths, baseURL_spec, rel_property_spec_stack, rel_property_specs)
                            query_paths = leftover_query_paths
                        else:
                            entity_url_property_spec = EntityURLSpec('#%s' % entity_name, self)
                            self.add_query_paths(query_paths, entity_url_property_spec, rel_property_spec_stack, rel_property_specs)
                    if len(query_paths) > 0:
                        sys.exit('query paths not valid or listed more than once: %s' % query_paths)  
        if not self.swagger_uris:
            del self.swagger['x-uris']
        return self.swagger

    def get_relationship_property_specs(self, entity_uri, entity_spec):
        spec = self.rapier_spec
        result = []
        if 'properties' in entity_spec:
            for prop_name, property in entity_spec['properties'].iteritems():
                if 'x-rapier-relationship' in property:
                    relationship = as_relationship(property['x-rapier-relationship'])
                    for target_entity_uri in as_list(relationship['entities']):
                        p_spec = \
                            RelMVPropertySpec(
                                self.conventions, 
                                prop_name,
                                entity_uri,
                                target_entity_uri, 
                                relationship.get('multiplicity', '1').split(':')[-1], 
                                property.get('implementation_private', False), 
                                relationship.get('collection_resource'), 
                                relationship.get('consumes'), 
                                relationship.get('readOnly'),
                                relationship.get('multi_valued_relationship_entity')) \
                        if relationship.get('multiplicity', '1').split(':')[-1] == 'n' else \
                            RelSVPropertySpec(self.conventions, 
                                prop_name,
                                entity_uri,
                                target_entity_uri, 
                                relationship.get('multiplicity', '1').split(':')[0], 
                                property.get('implementation_private', False), 
                                relationship.get('readOnly'))
                        p_spec.from_property = True
                        result.append(p_spec)
        return result
        
    def add_query_paths(self, query_paths, prefix, rel_property_spec_stack, prev_rel_property_specs):
        rapier_spec = self.rapier_spec
        rel_property_spec = rel_property_spec_stack[-1]
        target_entity_uri = rel_property_spec.target_entity
        target_entity_spec = self.resolve_entity(target_entity_uri)
        rel_property_specs = self.get_relationship_property_specs(target_entity_uri, target_entity_spec)
        for query_path in query_paths[:]:
            if query_path.matches(rel_property_spec_stack):
                self.emit_query_path(prefix, query_path, rel_property_spec_stack, prev_rel_property_specs)
                query_paths.remove(query_path)
        for rel_spec in rel_property_specs:
            if rel_spec not in rel_property_spec_stack:
                rel_property_spec_stack.append(rel_spec)
                self.add_query_paths(query_paths, prefix, rel_property_spec_stack, rel_property_specs)
                rel_property_spec_stack.pop()
                
    def emit_query_path(self, prefix, query_path, rel_property_spec_stack, rel_property_specs):
        for inx, spec in enumerate(rel_property_spec_stack):
            if spec.is_multivalued() and not query_path.query_segments[inx].param and not inx == len(rel_property_spec_stack) - 1:
                sys.exit('query path has multi-valued segment with no parameter: %s' % query_path)
        is_collection_resource = rel_property_spec_stack[-1].is_collection_resource() and not query_path.query_segments[-1].param
        path = '/'.join([prefix.path_segment(), query_path.swagger_path_string])
        is_private = reduce(lambda x, y: x or y.is_private(), rel_property_spec_stack, False)
        if not (self.include_impl and prefix.is_uri_spec()) and not (is_private and not self.include_impl):
            paths = self.swagger_uris if prefix.is_uri_spec() else self.swagger_paths 
            if path not in paths:
                if is_collection_resource:
                    paths[path] = self.build_relationship_interface(prefix, query_path, rel_property_spec_stack, rel_property_specs)
                else:
                    paths[path] = self.build_entity_interface(prefix, query_path, rel_property_spec_stack)
                    
    def build_entity_interface(self, prefix, query_path=None, rel_property_spec_stack=[], rel_property_specs=[]):
        entity_uri = rel_property_spec_stack[-1].target_entity if rel_property_spec_stack else prefix.target_entity
        entity_spec = self.resolve_entity(entity_uri)
        consumes = as_list(entity_spec['consumes']) if 'consumes' in entity_spec else None 
        produces = as_list(entity_spec['produces']) if 'produces' in entity_spec else None 
        query_parameters = entity_spec.get('query_parameters') 
        structured = 'type' not in entity_spec
        response_200 = {
            'schema': {} if len(rel_property_specs) > 1 else self.global_definition_ref(entity_uri)
            }
        if len(rel_property_specs) > 1:
            response_200['schema']['x-oneOf'] = [self.global_definition_ref(spec.target_entity) for spec in rel_property_specs]
        if not self.yaml_merge:
            response_200.update(self.responses.get('standard_200'))
        else:
            response_200['<<'] = self.responses.get('standard_200')
        path_spec = PresortedOrderedDict()
        is_private = reduce(lambda x, y: x or y.is_private(), rel_property_spec_stack, False)
        if is_private:
            path_spec['x-private'] = True
            x_description = 'This path is NOT part of the API. It is used in the implementaton and may be ' \
                'important to implementation-aware software, such as proxies or specification-driven implementations.'
        else:
            x_description = prefix.x_description()
        if x_description:
            path_spec['x-description'] = x_description
        parameters = self.build_parameters(prefix, query_path)
        if parameters:
            path_spec['parameters'] = parameters
        path_spec['get'] = {
                'description': 'Retrieve %s' % articled(self.resolve_entity_name(entity_uri)),
                'parameters': [{'$ref': '#/parameters/Accept'}],
                'responses': {
                    '200': response_200, 
                    }
                }
        if produces:
            path_spec['get']['produces'] = produces
        if query_parameters:
            path_spec['get']['parameters'] = [{k: v for d in [{'in': 'query'}, query_parameter] for k, v in d.iteritems()} for query_parameter in query_parameters]
        if not self.yaml_merge:
            path_spec['get']['responses'].update(self.response_sets['entity_get_responses'])
        else:
            path_spec['get']['responses']['<<'] = self.response_sets['entity_get_responses']
        immutable = entity_spec.get('readOnly', False)
        if not immutable:
            if structured:
                update_verb = 'patch'
                description = 'Update %s entity'
                parameter_ref = '#/parameters/If-Match'
                body_desciption =  'The subset of properties of the %s being updated' % self.resolve_entity_name(entity_uri)
            else:
                update_verb = 'put'
                description = 'Create or Update %s entity'
                self.define_put_if_match_header()
                parameter_ref = '#/parameters/Put-If-Match'
                body_desciption =  'The representation of the %s being replaced' % self.resolve_entity_name(entity_uri)
            schema = self.global_definition_ref(entity_uri)
            description = description % articled(self.resolve_entity_name(entity_uri))
            path_spec[update_verb] = {
                'description': description,
                'parameters': [
                    {'$ref': parameter_ref}, 
                    {'name': 'body',
                    'in': 'body',
                    'description': body_desciption,
                    'schema': schema
                    }
                    ],
                'responses': { 
                    '200': response_200
                    }
                }
            if not self.yaml_merge:
                path_spec[update_verb]['responses'].update(self.response_sets['put_patch_responses'])
            else:
                path_spec[update_verb]['responses']['<<'] = self.response_sets['put_patch_responses']
            if structured:
                path_spec['patch']['consumes'] = self.patch_consumes
            else:
                path_spec['put']['responses']['201'] = {
                    'description': 'Created new %s' % self.resolve_entity_name(entity_uri),
                    'schema': self.global_definition_ref(entity_uri),
                    'headers': {
                        'Location': {
                            'type': 'string',
                            'description': 'perma-link URL of newly-created %s'  % self.resolve_entity_name(entity_uri)
                            }
                        }
                    }
                if consumes:
                    path_spec[update_verb]['consumes'] = consumes

            if produces:
                path_spec[update_verb]['produces'] = produces
        well_known = entity_spec.get('well_known_URLs')
        if not well_known and not immutable:        
            path_spec['delete'] = {
                'description': 'Delete %s' % articled(self.resolve_entity_name(entity_uri)),
                'responses': {
                    '200': response_200
                    }
                }
            if produces:
                path_spec['delete']['produces'] = produces
            if not self.yaml_merge:
                path_spec['delete']['responses'].update(self.response_sets['delete_responses'])
            else:
                path_spec['delete']['responses']['<<'] = self.response_sets['delete_responses']
        path_spec['head'] = {
                'description': 'retrieve HEAD'
                }
        if not self.yaml_merge:
            path_spec['head'].update(self.methods['head'])
        else:
            path_spec['head']['<<'] = self.methods['head']
        path_spec['options'] = {
                'description': 'Retrieve OPTIONS',
               }
        if not self.yaml_merge:
            path_spec['options'].update(self.methods['options'])
        else:
            path_spec['options']['<<'] = self.methods['options']        
        return path_spec

    def build_relationship_interface(self, prefix, query_path, rel_property_spec_stack, rel_property_specs):
        rel_property_spec = rel_property_spec_stack[-1] if rel_property_spec_stack else prefix
        relationship_name = rel_property_spec.property_name
        entity_uri = rel_property_spec.target_entity
        entity_spec = self.resolve_entity(entity_uri)
        path_spec = PresortedOrderedDict()
        is_private = reduce(lambda x, y: x or y.is_private(), rel_property_spec_stack, False)
        if is_private:
            path_spec['x-private'] = True            
        parameters = self.build_parameters(prefix, query_path) 
        if parameters:
            path_spec['parameters'] = parameters
        path_spec['get'] = self.build_collection_get(rel_property_spec)
        rel_property_specs = [spec for spec in rel_property_specs if spec.property_name == relationship_name]
        consumes_entities = [entity for spec in rel_property_specs for entity in spec.consumes_entities]
        consumes_media_types = [media_type for spec in rel_property_specs if spec.consumes_media_types for media_type in spec.consumes_media_types]
        if len(rel_property_specs) > 1:
            schema = {}
            schema['x-oneOf'] = [self.global_definition_ref(spec.target_entity) for spec in rel_property_specs]
            i201_description = 'Created new %s' % ' or '.join([self.resolve_entity_name(spec.target_entity) for spec in rel_property_specs])
            location_desciption =  'perma-link URL of newly-created %s' % ' or '.join([self.resolve_entity_name(spec.target_entity) for spec in rel_property_specs])
            body_desciption =  'The representation of the new %s being created' % ' or '.join([self.resolve_entity_name(spec.target_entity) for spec in rel_property_specs])
        else:    
            schema = self.global_definition_ref(entity_uri)
            i201_description = 'Created new %s' % self.resolve_entity_name(entity_uri)
            location_desciption = 'perma-link URL of newly-created %s'  % self.resolve_entity_name(entity_uri)
            body_desciption =  'The representation of the new %s being created' % self.resolve_entity_name(entity_uri) 
        if not rel_property_spec.readonly:
            if len(consumes_entities) > 1:
                post_schema = {}
                post_schema['x-oneOf'] = [self.global_definition_ref(consumes_entity) for consumes_entity in consumes_entities]
                description = 'Create a new %s' % ' or '.join([self.resolve_entity_name(rel_prop_spec.target_entity) for rel_prop_spec in rel_property_specs])
            else:
                post_schema = self.global_definition_ref(entity_uri)
                description = 'Create a new %s' % self.resolve_entity_name(entity_uri)
            path_spec['post'] = {
                'description': description,
                'parameters': [
                    {'name': 'body',
                     'in': 'body',
                     'description': body_desciption,
                     'schema': post_schema
                    }
                    ],
                'responses': {
                    '201': {
                        'description': i201_description,
                        'schema': schema,
                        'headers': {
                            'Location': {
                                'type': 'string',
                                'description': location_desciption
                                },
                            'ETag': {
                                'type': 'string',
                                'description': 'Value of ETag required for subsequent updates'
                                }
                            }
                        }
                    }                
                }
            if consumes_media_types:
                path_spec['post']['consumes'] = consumes_media_types
            if not self.yaml_merge:
                path_spec['post']['responses'].update(self.response_sets['post_responses'])
            else:
                path_spec['post']['responses']['<<'] = self.response_sets['post_responses']
        path_spec['head'] = {
                'description': 'Retrieve HEAD'
                }
        if not self.yaml_merge:
            path_spec['head'].update(self.methods['head'])
        else:
            path_spec['head']['<<'] = self.methods['head']
        path_spec['options'] = {
                'description': 'Retrieve OPTIONS',
               }
        if not self.yaml_merge:
            path_spec['options'].update(self.methods['options'])
        else:
            path_spec['options']['<<'] = self.methods['options']            
        return path_spec
        
    def build_standard_response_sets(self):
        result = dict()
        result['entity_get_responses'] = {
            '401': self.global_response_ref('401'), 
            '403': self.global_response_ref('403'), 
            '404': self.global_response_ref('404'), 
            '406': self.global_response_ref('406'), 
            'default': self.global_response_ref('default')
            }
        result['put_patch_responses'] = {
            '400': self.global_response_ref('400'),
            '401': self.global_response_ref('401'), 
            '403': self.global_response_ref('403'), 
            '404': self.global_response_ref('404'), 
            '406': self.global_response_ref('406'), 
            '409': self.global_response_ref('409'),
            'default': self.global_response_ref('default')
            }  
        result['delete_responses'] = {
            '400': self.global_response_ref('400'),
            '401': self.global_response_ref('401'), 
            '403': self.global_response_ref('403'), 
            '404': self.global_response_ref('404'), 
            '406': self.global_response_ref('406'), 
            'default': self.global_response_ref('default')
            }
        result['post_responses'] = {        
            '400': self.global_response_ref('400'),
            '401': self.global_response_ref('401'), 
            '403': self.global_response_ref('403'), 
            '404': self.global_response_ref('404'), 
            '406': self.global_response_ref('406'), 
            'default': self.global_response_ref('default')
            }
        return result

    def build_standard_methods(self):
        result = dict()
        result['head'] = {
            'responses': {
                '200': self.global_response_ref('standard_200'), 
                '401': self.global_response_ref('401'), 
                '403': self.global_response_ref('403'), 
                '404': self.global_response_ref('404'), 
                'default': self.global_response_ref('default')
                }
            }
        result['options'] = {
            'parameters': [ 
                {'$ref': '#/parameters/Access-Control-Request-Method'}, 
                {'$ref': '#/parameters/Access-Control-Request-Headers'} 
                ],
            'responses': {
                '200': self.global_response_ref('options_200'), 
                '401': self.global_response_ref('401'), 
                '403': self.global_response_ref('403'), 
                '404': self.global_response_ref('404'), 
                'default': self.global_response_ref('default')
                }
            }

        return result

    def global_response_ref(self, key):
        if key not in self.swagger['responses']:
             self.swagger['responses'][key] = self.responses[key]
        return {'$ref': '#/responses/%s' % key}

    def global_definition_ref(self, key):
        return {'$ref': self.swagger_uri_map[key]}
        
    def build_parameters(self, prefix, query_path):
        result = []
        param = prefix.build_param()
        if param:
            if isinstance(param, list):
                result.extend(param)
            else:
                result.append(param)
        if query_path:
            for query_segment in query_path.query_segments:
                param = query_segment.build_param()
                if param:
                    result.append(param)
        return result
          
    def build_standard_responses(self):
        return {
            'standard_200': {
                'description': 'successful',
                'headers': {
                    'Content-Location': {
                        'type': 'string',
                        'description': 'perma-link URL of resource'
                        },
                    'ETag': {
                        'description': 'this value must be echoed in the If-Match header of every PATCH or PUT',
                        'type': 'string'
                        }
                    }
                },
            'options_200': {
                'description': 'successful',
                'headers': {
                    'Access-Control-Allow-Origin': {
                        'type': 'string',
                        'description': 'origins allowed'
                        },
                    'Access-Control-Allow-Methods': {
                        'description': 'methods allowed',
                        'type': 'string'
                        },
                    'Access-Control-Allow-Headers': {
                        'description': 'headers allowed',
                        'type': 'string'
                        },
                    'Access-Control-Max-Age': {
                        'description': 'length of time response can be cached',
                        'type': 'string'
                        }
                    }
                },
            '303': {
                'description': 'See other. Server is redirecting client to a different resource',
                'headers': {
                    'Location': {
                        'type': 'string',
                        'description': 'URL of other resource'
                        }
                    }
                },
            '400': {
                'description': 'Bad Request. Client request in error',
                'schema': self.error_response
                },
            '401': {
                'description': 'Unauthorized. Client authentication token missing from request',
                'schema': self.error_response
                }, 
            '403': {
                'description': 'Forbidden. Client authentication token does not permit this method on this resource',
                'schema': self.error_response
                }, 
            '404': {
                'description': 'Not Found. Resource not found',
                'schema': self.error_response
                }, 
            '406': {
                'description': 'Not Acceptable. Requested media type not available',
                'schema': self.error_response
                }, 
            '409': {
                'description': 'Conflict. Value provided in If-Match header does not match current ETag value of resource',
                'schema': self.error_response
                }, 
            'default': {
                'description': '5xx errors and other stuff',
                'schema': self.error_response
                }
            }
        
    def build_collection_get(self, rel_property_spec):
        collection_entity_uri = rel_property_spec.multi_valued_relationship_entity
        if not collection_entity_uri:
            sys.exit('must provide multi_valued_relationship_entity for property %s in entity %s in spec %s' % (rel_property_spec.property_name, rel_property_spec.source_entity, self.filename))
        if collection_entity_uri not in self.uri_map:
            sys.exit('error: must define entity %s' % collection_entity_uri)   
        else:
            collection_entity = self.resolve_entity(collection_entity_uri)
        rslt = {
            'responses': {
                '200': {
                    'description': 'description',
                    'schema': json_ref(self.swagger_uri_map[collection_entity_uri]),
                    'headers': {
                        'Content-Location': {
                            'type': 'string',
                            'description': 'perma-link URL of collection'
                            }
                        }
                    }, 
                '303': self.global_response_ref('303'),
                '401': self.global_response_ref('401'), 
                '403': self.global_response_ref('403'), 
                '404': self.global_response_ref('404'), 
                '406': self.global_response_ref('406'), 
                'default': self.global_response_ref('default')
                }
            }
        def add_query_parameters(entity, query_params):
            if 'query_parameters' in entity:
                query_params.extend(entity['query_parameters'])
            if 'oneOf' in entity:
                for entity_ref in entity['oneOf']:
                    add_query_parameters(self.resolve_entity_ref(entity_ref), query_params) 
        query_parameters = []
        add_query_parameters(collection_entity, query_parameters)
        query_parameters = {param['name']: param for param in query_parameters}.values() #get rid of duplicates
        if query_parameters:
            rslt['parameters'] = [{k: v for d in [{'in': 'query'}, query_parameter] for k, v in d.iteritems()} for query_parameter in query_parameters]
        return rslt        
 
    def define_put_if_match_header(self):
        if not 'Put-If-Match' in self.header_parameters:
            self.header_parameters['Put-If-Match'] = {
                'name': 'If-Match',
                'in': 'header',
                'type': 'string',
                'description': 'specifies the last known ETag value of the resource being modified',
                'required': False
                }
    
    def build_standard_header_parameters(self):
        return {
            'If-Match': {
                'name': 'If-Match',
                'in': 'header',
                'type': 'string',
                'description': 'specifies the last known ETag value of the resource being modified',
                'required': True
                },
            'Accept': {
                'name': 'Accept',
                'in': 'header',
                'type': 'string',
                'description': 'specifies the requested media type - required',
                'required': True
                },
            'Access-Control-Request-Method': {
                'name': 'Access-Control-Request-Method',
                'description': 'specifies the method the client wishes to use',
                'in': 'header',
                'required': True,
                'type': 'string'
                },
            'Access-Control-Request-Headers': {
                'name': 'Access-Control-Request-Headers',
                'description': 'specifies the custom headers the client wishes to use',
                'in': 'header',
                'required': True,
                'type': 'string'
                }
            }
                
    def resolve_entity(self, uri):
        return self.uri_map[uri]

    def resolve_entity_ref(self, ref):
        return self.resolve_entity(ref['$ref'])

    def resolve_entity_name(self, uri):
        return self.uri_map[uri]['name']

    def resolve_property(self, entity_uri, property_name):
        entity = self.resolve_entity(entity_uri)
        if not entity:
            return None
        if 'properties' in entity:
            if property_name in entity['properties']:
                return entity['properties'][property_name]
        if 'allOf' in entity:
            for entity_ref in entity['allOf']:
                property = self.resolve_property(entity_ref['$ref'], property_name)
                if property:
                    return property

class SegmentSpec(object):
            
    def __eq__(self, other):
        if type(other) is type(self):
            return self.__dict__ == other.__dict__
        return False

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return self.__dict__.hash()

    def __str__(self):
        return 'SegmentSpec(%s)' % self.__dict__.__str__()

    def __repr__(self):
        return '%s(%s)' % (self.__class__.__name__, ', '.join(['%s=%s' % item for item in self.__dict__.iteritems()]))
        
class PathPrefix(object):
            
    def build_param(self):
        return None  
        
    def __eq__(self, other):
        if type(other) is type(self):
            return self.__dict__ == other.__dict__
        return False

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return self.__dict__.hash()

    def __str__(self):
        return self.__dict__.str()

    def __repr__(self):
        return '%s(%s)' % (self.__class__.__name__, ', '.join(['%s=%s' % item for item in self.__dict__.iteritems()]))
        
    def x_description(self):
        return None
        
    def is_private(self):
        return False
        
    def is_uri_spec(self):
        return False
      
class RelSVPropertySpec(SegmentSpec):
    
    def __init__(self, conventions, property_name, source_entity, target_entity, multiplicity, implementation_private, readonly):
        self.property_name = property_name
        self.source_entity = source_entity
        self.target_entity = target_entity
        self.multiplicity = multiplicity
        self.implementation_private = implementation_private
        self.readonly = readonly 
        
    def is_multivalued(self):
        return False
        
    def is_collection_resource(self):
        return False
        
    def get_multiplicity(self):
        return self.multiplicity
                
    def is_private(self):
        return self.implementation_private
                
class RelMVPropertySpec(SegmentSpec):
    
    def __init__(self, conventions, property_name, source_entity, target_entity, multiplicity, implementation_private, collection_resource, consumes, readonly, multi_valued_relationship_entity):
        self.property_name = property_name
        self.source_entity = source_entity
        self.target_entity = target_entity
        self.multiplicity = multiplicity
        self.implementation_private = implementation_private
        self.readonly = readonly 
        self.conventions = conventions
        self.collection_resource = True if collection_resource == None else collection_resource
        self.consumes = consumes
        self.consumes_media_types = consumes.keys() if isinstance(consumes, dict) else as_list(consumes) if consumes is not None else None
        self.consumes_entities = [entity for entity_list in consumes.values() for entity in as_list(entity_list)] if isinstance(consumes, dict) else [self.target_entity]
        self.multi_valued_relationship_entity = multi_valued_relationship_entity 

    def is_multivalued(self):
        return True
        
    def is_private(self):
        return self.implementation_private
        
    def is_collection_resource(self):
        return not not self.collection_resource
            
    def get_multiplicity(self):
        return self.multiplicity

    def __eq__(self, other):
        if type(other) is type(self):
            return self.__dict__ == other.__dict__
        return False

    def __hash__():
        return self.__dict__.hash()
        
    def __ne__(self, other):
        return not self.__eq__(other)
        
class WellKnownURLSpec(PathPrefix):
    
    def __init__(self, base_URL, target_entity):
        self.base_URL = base_URL 
        self.target_entity = target_entity

    def path_segment(self, select_one_of_many = False):
        return self.base_URL[1:] if self.base_URL.endswith('/') else self.base_URL

    def build_param(self):
        input_string = self.base_URL
        param_name = ''
        params = []
        while param_name is not None:
            param_name, next_start = get_param_name(input_string)
            if param_name:
                params.append({
                'name': param_name,
                'in': 'path',
                'type': 'string',
                'required': True
                })
                input_string = input_string[next_start:]
        return params

class QueryPath(object):

    def __init__(self, query_path, generator):
        self.query_segments = list()
        segments = (query_path['segments'] if hasattr(query_path, 'keys') else query_path).split('/')
        separator = query_path.get('discriminator_separator', generator.discriminator_separator) if hasattr(query_path, 'keys') else generator.discriminator_separator
        for segment in segments:
            self.query_segments.append(QuerySegment(segment, self.query_segments, separator, generator))
        self.swagger_path_string = '/'.join([query_segment.swagger_segment_string for query_segment in self.query_segments])
            
    def matches(self, rel_stack):
        if len(self.query_segments) == len(rel_stack):
            for segment, rel_spec in itertools.izip(self.query_segments, rel_stack):
                if segment.property_name != rel_spec.property_name:
                    return False
            for segment, rel_spec in itertools.izip(self.query_segments, rel_stack):
                segment.rel_property_spec = rel_spec            
            return True
        else:
            return False
            
    def __str__(self):
        return self.__dict__.str()

    def __repr__(self):
        return '%s(%s)' % (self.__class__.__name__, ', '.join(['%s=%s' % item for item in self.__dict__.iteritems()]))
        
class QuerySegment(object):

    def __init__(self, query_segment_string, query_segments, separator, generator):
        self.generator = generator
        parts = query_segment_string.split(';')
        if len(parts) > 2:
            sys.exit('query path segment contains more than 1 ; - %s' % query_segment_string)
        elif len(parts) == 2:
            params_part = parts[1]
            if '{' in params_part:
                open_brace_offset = params_part.index('{')
                if '}' in params_part:
                    close_brace_offset = params_part.index('}')
                    if open_brace_offset < close_brace_offset:
                        self.discriminator_property_name = params_part[open_brace_offset+1 : close_brace_offset]
                    else:
                        sys.exit('empty path parameter ({}) - %s' % segment_string)
                else:
                    sys.exit('no closing { for path paramter - %s' % segment_string)
            else:
                sys.exit('missing path paramter ({xxx}) - %s' % segment_string)
            self.param = self.discriminator_property_name 
            duplicate_count = len([query_segement.param == self.param for query_segement in query_segments])
            if duplicate_count > 0:
                self.param = '_'.join((self.param, str(duplicate_count)))
                params_part = self.param.join((params_part[:open_brace_offset+1], params_part[close_brace_offset:]))
            self.swagger_segment_string = separator.join((parts[0], params_part))
        else:
            self.discriminator_property_name = None
            self.param = None
            self.swagger_segment_string = query_segment_string
        self.property_name = parts[0]      

    def build_param(self):
        if self.param:
            property = self.generator.resolve_property(self.rel_property_spec.target_entity, self.discriminator_property_name)
            if not property:
                print >> sys.stderr, self.property_name, self.param
                sys.exit('Property named %s not found in Entity %s in file %s' % (self.property_name, self.rel_property_spec.target_entity, self.generator.filename))
            rslt = {
                'name': self.param,
                'in': 'path',
                'type': property['type'],
                'required': True
                } 
            if self.rel_property_spec.implementation_private:
                rslt['description'] = 'This parameter is a private part of the implementation. It is not part of the API'
            return rslt
        else:
            return None

    def __str__(self):
        return self.__dict__.str()

    def __repr__(self):
        return '%s(%s)' % (self.__class__.__name__, ', '.join(['%s=%s' % item for item in self.__dict__.iteritems()]))
        
class EntityURLSpec(PathPrefix):
    
    def __init__(self, target_entity, swagger_generator):
        self.target_entity = target_entity
        self.swagger_generator = swagger_generator

    def path_segment(self, select_one_of_many = False):
        return '{%s_URL}' % self.swagger_generator.resolve_entity_name(self.target_entity)

    def build_param(self):
        return {
            'name': '%s_URL' % self.swagger_generator.resolve_entity_name(self.target_entity),
            'in': 'URL',
            'type': 'string',
            'description':
                "The URL of %s entity" % articled(self.swagger_generator.resolve_entity_name(self.target_entity)),
            'required': True
            }
            
    def is_uri_spec(self):
        return True
 
def as_list(value, separator = None):
    if isinstance(value, basestring):
        if separator:
            result = [item.strip() for item in value.split(separator)]
        else:
            result = value.split()
    else:
        if isinstance(value, (list, tuple)):
            result = value
        else:
            result = [value]
    return result
    
def as_relationship(input):
    if hasattr(input, 'keys'):
        return input
    else:
        return {'entities': input}
        
def main(args):
    generator = SwaggerGenerator()
    try:
        opts, args = getopt.getopt(sys.argv[1:], 'maivs', ['yaml-merge', 'yaml-alias', 'include-impl', 'suppress-annotations'])
    except getopt.GetoptError as err:
        usage = '\nusage: gen_swagger.py [-m, --yaml-merge] [-a, --yaml-alias] [-i, --include-impl] [-n, --suppress-annotations] filename'
        sys.exit(str(err) + usage)
    generator.set_rapier_spec_from_filename(*args)
    generator.set_opts(opts)
    Dumper = yaml.SafeDumper
    opts_keys = [k for k,v in opts]
    if '--yaml-alias' not in opts_keys and '-m' not in opts_keys:
        Dumper.ignore_aliases = lambda self, data: True
    Dumper.add_representer(PresortedOrderedDict, yaml.representer.SafeRepresenter.represent_dict)
    print str.replace(yaml.dump(generator.swagger_from_rapier(), default_flow_style=False, Dumper=Dumper), "'<<':", '<<:')
    
def article(name):
    return 'an' if name[0].lower() in 'aeiou' else 'a'
        
def articled(name):
    return '%s %s' % (article(name), name)
    
def get_param_name(input_string):
    if '{' in input_string:
        open_brace_offset = input_string.index('{')
        if '}' in input_string:
            close_brace_offset = input_string.index('}')
            if open_brace_offset < close_brace_offset:
                param = input_string[open_brace_offset+1 : close_brace_offset]
            else:
                sys.exit('empty path parameter ({}) - %s' % segment_string)
        else:
            sys.exit('no closing { for path paramter - %s' % segment_string)
    else:
        param = None
        close_brace_offset = -1
    return param, close_brace_offset + 1
                
def json_ref(key):
    return {'$ref': key}
        
if __name__ == "__main__":
    main(sys.argv)