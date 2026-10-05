#!/usr/bin/env bash
# Destructive fixture loader: ONLY for a disposable stuntdb_test database.
set -euo pipefail
case "${1:-}" in
  sakila|employees) zoo_sample="$1" ;;
  *) echo 'Usage: load_zoo.sh sakila|employees' >&2; exit 1 ;;
esac
zoo_container="${STUNTDB_MYSQL_CONTAINER:?Set STUNTDB_MYSQL_CONTAINER to the disposable test container}"
zoo_client=$(docker exec "$zoo_container" sh -c 'command -v mariadb || command -v mysql')
zoo_temp=$(mktemp -d)
trap 'rm -rf "$zoo_temp"' EXIT
if [ "$zoo_sample" = sakila ]; then
  curl -fsSL --retry 3 https://downloads.mysql.com/docs/sakila-db.tar.gz -o "$zoo_temp/sample.tar.gz"
  python3 - "$zoo_temp/sample.tar.gz" <<'PY'
import hashlib, pathlib, sys
assert hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest() == '683e957d456341e48cccd66135ddb0d5701a8436523ed0a6366279f8e8719a68', 'Sakila archive changed; review before updating the checksum'
PY
  tar -xzf "$zoo_temp/sample.tar.gz" -C "$zoo_temp"
  zoo_directory="$zoo_temp/sakila-db"
  for zoo_file in sakila-schema.sql sakila-data.sql; do
    sed -E 's/(SCHEMA (IF EXISTS )?)sakila/\1stuntdb_test/;s/^USE sakila;/USE stuntdb_test;/;s/sakila\./stuntdb_test./g' "$zoo_directory/$zoo_file" |
      docker exec -i "$zoo_container" "$zoo_client" -uroot -ptest-only-password
  done
else
  git clone --quiet https://github.com/datacharmer/test_db.git "$zoo_temp/employees"
  git -C "$zoo_temp/employees" checkout --quiet e324b56193ca506ab7cc1ab143a9153d8c4535d7
  zoo_directory="$zoo_temp/employees"
  sed -E 's/(DATABASE (IF (NOT )?EXISTS )?)employees/\1stuntdb_test/;s/^USE employees;/USE stuntdb_test;/' "$zoo_directory/employees.sql" > "$zoo_directory/stuntdb-load.sql"
  # SOURCE paths in upstream SQL are relative to the mysql client's working dir.
  docker exec "$zoo_container" mkdir -p /tmp/stuntdb-employees
  docker cp "$zoo_directory/." "$zoo_container:/tmp/stuntdb-employees"
  docker exec -i -w /tmp/stuntdb-employees "$zoo_container" "$zoo_client" -uroot -ptest-only-password < "$zoo_directory/stuntdb-load.sql"
fi
