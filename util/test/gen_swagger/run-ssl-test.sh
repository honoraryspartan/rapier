DIR=$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd)
#echo $DIR
ROOT_DIR=$( cd "$( dirname "$DIR/../../../../../" )" && pwd)
cd $ROOT_DIR
#echo $ROOT_DIR
./rapier/util/gen_swagger.py -s rapier/util/test/ssl.yaml > rapier/util/test/gen_swagger/swagger-ssl.yaml
# ./rapier/util/gen_swagger.py -is rapier/util/test/spec-hub.yaml > rapier/util/test/gen_swagger/swagger-spec-hub-with-impl.yaml