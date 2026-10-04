# Schema zoo

The matrix runs on MySQL 8.0 and 8.4 using only the disposable `stuntdb_test` database. Each sample is loaded afresh in a separate CI job. Tests extract a slice, rebuild an empty base-table schema from `SHOW CREATE TABLE` (without triggers, routines or views), restore the SQL, compare every row and run independent foreign-key anti-join checks. Sakila and Employees are restored both unmasked and with reviewed sample-only masking rules.

| Case | Source | Coverage |
| --- | --- | --- |
| Sakila | Full official archive, SHA-256 pinned in the loader | Rental/payment closure, staff/store cycle, spatial columns, SET, ENUM, decimal values, timestamps |
| Employees | Full datacharmer/test_db at e324b56193ca506ab7cc1ab143a9153d8c4535d7 | 300k+ source employees, salary/title children, composite keys, departments, dates and ENUM |
| Frameworks | Original synthetic DDL in frameworks.sql | Django-shaped membership and self-reference, generic relation boundary, WordPress-shaped unconstrained links |

Configured sample masking explicitly retains unsupported enum/SET, spatial and birth-date fields; it is partial masking, not full anonymization.

The framework fixture is not a Django or WordPress installation. It explicitly asserts that Django generic object IDs and WordPress application-only relationships are **not followed**. Declared relationships are scheduled for v0.3; no application-level completeness claim is made for these links.

Upstream sample files are downloaded only into temporary directories and are not distributed under stuntdb's Apache license. Sakila retains the license notice in its archive; Employees is CC BY-SA 3.0, with upstream attribution and license in its repository. The loader rewrites database identifiers only to isolate the imports in the disposable database. The Sakila hash intentionally fails if the upstream archive changes.

Sources:

- https://dev.mysql.com/doc/sakila/en/sakila-installation.html
- https://github.com/datacharmer/test_db/tree/e324b56193ca506ab7cc1ab143a9153d8c4535d7
- https://docs.djangoproject.com/en/5.2/ref/contrib/contenttypes/
- https://github.com/WordPress/WordPress/blob/master/wp-admin/includes/schema.php

Local reproduction (destroys `stuntdb_test` and uses a test-only root password):

```sh
docker run --rm -d --name stuntdb-zoo -e MYSQL_ROOT_PASSWORD=test-only-password \
  -e MYSQL_DATABASE=stuntdb_test -p 127.0.0.1:33061:3306 mysql:8.4
# Wait until MySQL is ready.
STUNTDB_MYSQL_CONTAINER=stuntdb-zoo scripts/load_zoo.sh sakila
STUNTDB_ZOO_URL='mysql+pymysql://root:test-only-password@127.0.0.1:33061/stuntdb_test' \
  STUNTDB_ZOO_SAMPLE=sakila python -m pytest -q -m zoo
# Reload the dataset before repeating: each round trip replaces the source.
# Repeat with employees, or run sample=frameworks (no loader needed).
docker stop stuntdb-zoo
```
