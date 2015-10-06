DIR=$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd)
echo $DIR
ROOT_DIR=$( cd "$( dirname "$DIR/../../../../../" )" && pwd)
cd $ROOT_DIR
echo $ROOT_DIR
python rapier/util/gen_swagger.py --no-merge --no-alias rapier/util/test/spec-hub.yaml > rapier/util/test/gen_swagger/swagger-spec-hub.yaml