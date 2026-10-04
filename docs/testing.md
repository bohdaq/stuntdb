# Testing

Run the unit suite with `python -m pytest -q -m 'not mysql'`.

The MySQL suite drops and rebuilds fixture tables. It requires a disposable database named exactly `stuntdb_test`; never point it at a real database. Example with Docker:

```sh
docker run --rm -d --name stuntdb-mysql-test \
  -e MYSQL_ROOT_PASSWORD=test-only-password \
  -e MYSQL_DATABASE=stuntdb_test \
  -p 127.0.0.1:33060:3306 mysql:8.4
# Wait for MySQL to be ready, then run:
STUNTDB_TEST_MYSQL_URL='mysql+pymysql://root:test-only-password@127.0.0.1:33060/stuntdb_test' \
  python -m pytest -q -m mysql
docker stop stuntdb-mysql-test
```

CI defines the same integration suite for MySQL 8.0 and 8.4. Fixtures cover mutual cycles, composite and nullable references, generated columns, special characters and a SQL round trip with restored foreign-key checks. Local unit fixtures also cover self-references, missing parents, row ceilings, cycle reporting and atomic output failure.

The full Sakila / Employees / Django / WordPress schema zoo remains pending. Configuring CI does not establish that integration tests pass; inspect the actual CI results before releasing.
