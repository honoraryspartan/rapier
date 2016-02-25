#!/usr/bin/env python 

from difflib import SequenceMatcher
from collections import OrderedDict
from collections import Counter
import sys
from yaml.composer import Composer
from yaml.reader import Reader
from yaml.scanner import Scanner
from yaml.composer import Composer
from yaml.resolver import Resolver
from yaml.parser import Parser
from yaml.constructor import Constructor, BaseConstructor, SafeConstructor
from urlparse import urlsplit

class PresortedList(list):
    def sort(self, *args, **kwargs):
        pass

class PresortedOrderedDict(OrderedDict):
    def items(self, *args, **kwargs):
        return PresortedList(OrderedDict.items(self, *args, **kwargs))

def create_node_class(cls):
    class node_class(cls):
        def __init__(self, x, start_mark, end_mark):
            cls.__init__(self, x)
            self.start_mark = start_mark
            self.end_mark = end_mark

        def __new__(self, x, start_mark, end_mark):
            return cls.__new__(self, x)
    node_class.__name__ = '%s_node' % cls.__name__
    return node_class

dict_node = create_node_class(dict)
list_node = create_node_class(list)
unicode_node = create_node_class(unicode)

class NodeConstructor(SafeConstructor):
    # To support lazy loading, the original constructors first yield
    # an empty object, then fill them in when iterated. Due to
    # laziness we omit this behaviour (and will only do "deep
    # construction") by first exhausting iterators, then yielding
    # copies.
    def construct_yaml_map(self, node):
        obj, = SafeConstructor.construct_yaml_map(self, node)
        return dict_node(obj, node.start_mark, node.end_mark)

    def construct_yaml_seq(self, node):
        obj, = SafeConstructor.construct_yaml_seq(self, node)
        return list_node(obj, node.start_mark, node.end_mark)

    def construct_yaml_str(self, node):
        obj = SafeConstructor.construct_scalar(self, node)
        assert isinstance(obj, unicode)
        return unicode_node(obj, node.start_mark, node.end_mark)

NodeConstructor.add_constructor(
        u'tag:yaml.org,2002:map',
        NodeConstructor.construct_yaml_map)

NodeConstructor.add_constructor(
        u'tag:yaml.org,2002:seq',
        NodeConstructor.construct_yaml_seq)

NodeConstructor.add_constructor(
        u'tag:yaml.org,2002:str',
        NodeConstructor.construct_yaml_str)


class MarkedLoader(Reader, Scanner, Parser, Composer, NodeConstructor, Resolver):
    def __init__(self, stream):
        Reader.__init__(self, stream)
        Scanner.__init__(self)
        Parser.__init__(self)
        Composer.__init__(self)
        SafeConstructor.__init__(self)
        Resolver.__init__(self)

class OASValidator(object):

    def __init__(self):
        self.errors = 0
        self.similarity_ratio = 0.7
        self.checked_id_uniqueness = False

    def validate_title(self, key, title):
        if not isinstance(title, basestring):
            self.error('title must be a string', key)

    def validate_version(self, key, version):
        if not isinstance(version, basestring):
            self.error('version must be a string ', key)
        
    def check_id_uniqueness(self):
        entities = set()
        non_entities = set()
        if 'entities' in self.rapier_spec:
            for name, entity in self.rapier_spec['entities'].iteritems():
                id = entity.get('id', name)
                if id in entities:
                    self.info('information about %s is provided in multiple places - is this what you meant?' % id)
                else:
                    entities.add(id)
        if 'non_entities' in self.rapier_spec:
            for name, entity in self.rapier_spec['non_entities'].iteritems():
                id = entity.get('id', name)
                if id in non_entities:
                    self.info('information about %s is provided in multiple places - is this what you meant?' % id)
                if id in entities:
                    self.error('%s is declared to be both an entity and a non_entity. It cannot be both' % id)
                else:
                    entities.add(id)
        self.checked_id_uniqueness = True
            
    def validate_entities(self, key, entities):
        if not self.checked_id_uniqueness:
            self.check_id_uniqueness()
        for entity in entities.itervalues():
            self.check_and_validate_keywords(self.__class__.entity_keywords, entity)

    def validate_non_entities(self, key, non_entities):
        if not self.checked_id_uniqueness:
            self.check_id_uniqueness()
        for non_entity in non_entities.itervalues():
            self.check_and_validate_keywords(self.__class__.entity_keywords, non_entity)

    def validate_conventions(self, key, conventions):
        if not hasattr(conventions, 'iteritems'):
            self.error('conventions must be a JSON object')
        self.check_and_validate_keywords(self.__class__.conventions_keywords, conventions)

    def validate_id(self, key, id):
        if not isinstance(id, basestring):
            self.error('id must be a string: %s' % id, key)

    def validate_query_paths(self, key, entities):
        self.info('query_paths not yet validated')

    def validate_well_known_URLs(self, key, urls):
        if not isinstance(urls, (basestring, list)):
            self.error('well_known_URLs must be a string or an array: %s' % id, key)
        else:
            if isinstance(urls, basestring):
                urls = urls.split()
            for url in urls:
                parsed_url = urlsplit(url)
                if parsed_url.scheme or parsed_url.netloc or not parsed_url.path.startswith('/'):
                    self.error('validate_well_known_URLs must be begin with a single slash %s' % url, key)

    def validate_properties(self, key, properties):
        #TODO validate property names
        for property in properties.itervalues():
            self.check_and_validate_keywords(self.__class__.property_keywords, property)

    def validate_readOnly(self, key, readOnly):
        if not (readOnly is True or readOnly is False) :
            self.error('id must be a boolean: %s' % readOnly, key)

    def validate_selector_location(self, key, location):
        if not location in ['path-segment', 'path-parameter']:
            self.error('%s must be either the string "path-segment" or "path-parameter"' % location)

    def similar(self, a, b):
        return SequenceMatcher(None, a, b).ratio() > self.similarity_ratio
    
    def check_and_validate_keywords(self, keyword_validators, spec):
        for key, value in spec.iteritems():
            if key not in keyword_validators:
                similar_keywords = [keyword for keyword in keyword_validators.iterkeys() if self.similar(key, keyword)]
                message = 'unrecognized keyword %s at line %s, column %s' % (key, key.start_mark.line + 1, key.start_mark.column + 1)
                if similar_keywords:
                    message += ' - did you mean %s?' % ' or '.join(similar_keywords)
                self.info(message)
            else:
                keyword_validators[key](self, key, value)        

    def validate_property_type(self, key, p_type):
        if not p_type in ['array', 'boolean', 'integer', 'number', 'null', 'object', 'string']:
            self.error("type must be one of 'array', 'boolean', 'integer', 'number', 'null', 'object', 'string': " % p_type, key)            
            
    def validate_property_format(self, key, format):
        if not isinstance(format, basestring):
            self.error('format must be a string: %s' % format, key)    
            
    def validate_property_relationship(self, key, relationship):
        self.info('relationship not yet validated')        
            
    def validate_property_items(self, key, items):
        self.info('items not yet validated')        
            
    rapier_spec_keywords = {'title': validate_title, 'entities': validate_entities, 'non_entities': validate_non_entities, 'conventions': validate_conventions, 'version': validate_version}
    entity_keywords = {'id': validate_id, 'query_paths': validate_query_paths, 'well_known_URLs': validate_well_known_URLs, 'properties': validate_properties, 'readOnly': validate_readOnly}
    non_entity_keywords = {'id': validate_id, 'properties': validate_properties, 'readOnly': validate_readOnly}
    conventions_keywords = {'selector_location': validate_selector_location}
    property_keywords = {'type': validate_property_type, 'format': validate_property_format, 'relationship': validate_property_relationship, 'items': validate_property_items, 'readOnly': validate_readOnly}

    def validate(self):
        if not hasattr(self.rapier_spec, 'keys'):
            self.fatal_error('rapier specification must be a YAML mapping: %s' % self.filename)
        self.check_and_validate_keywords(self.__class__.rapier_spec_keywords, self.rapier_spec)

    def marked_load(self, stream):
        def construct_mapping(loader, node):
            keys = [node_tuple[0].value for node_tuple in node.value]
            for item, count in Counter(keys).items():
                if count > 1:
                    key_nodes = [node_tuple[0] for node_tuple in node.value if node_tuple[0].value == item]
                    self.errors += 1
                    self.warning('%s occurs %s times, at %s' % (item, count, ' and '.join(['line %s, column %s' % (key_node.start_mark.line + 1, key_node.start_mark.column + 1) for key_node in key_nodes])))            
            loader.flatten_mapping(node)
            return PresortedOrderedDict(loader.construct_pairs(node))
        MarkedLoader.add_constructor(
            Resolver.DEFAULT_MAPPING_TAG,
            construct_mapping)
        return MarkedLoader(stream).get_single_data()
        
    def set_rapier_spec_from_filename(self, filename):
        self.filename = filename
        with open(filename) as f:
            self.rapier_spec = self.marked_load(f.read())
            
    def fatal_error(self, message):
        sys.exit(' '. join(['FATAL ERROR -', message, 'in', self.filename]))

    def error(self, message, key_node=None):
        self.errors += 1
        if key_node:
            message += ' after line %s column %s to line %s column %s' % (key_node.start_mark.line + 1, key_node.start_mark.column + 1, key_node.end_mark.line + 1, key_node.end_mark.column + 1)
        print >> sys.stderr, ' '. join(['ERROR -', message, 'in', self.filename])

    def warning(self, message):
        print >> sys.stderr, ' '. join(['WARNING -', message, 'in', self.filename])

    def info(self, message):
        print >> sys.stderr, ' '. join(['INFO -', message, 'in', self.filename])

def main(args):
    validator = OASValidator()
    validator.set_rapier_spec_from_filename(*args)

    validator.validate()

if __name__ == "__main__":
    main(sys.argv[1:])