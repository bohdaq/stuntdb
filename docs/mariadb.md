# MariaDB compatibility

Supported server series are MariaDB 10.11 and 11.4, alongside MySQL 8.0/8.4. Other MariaDB series fail snapshot/load validation until tested. Use `mariadb://` or `mariadb+pymysql://`; existing `mysql+pymysql://` URLs also detect the server family. All server URLs use the installed PyMySQL driver; no extra connector is required.

```sh
stuntdb init "$STUNTDB_SOURCE" -o stuntdb.json
stuntdb snapshot --config stuntdb.json --seed orders.id=42 -o slice.sql
stuntdb load slice.sql --target "$STUNTDB_TARGET"
stuntdb verify "$STUNTDB_TARGET" --manifest slice.sql.manifest.json
```

Manifests identify MariaDB separately from MySQL. A MySQL-family manifest cannot load into MariaDB or vice versa, even when a simple schema happens to match. This is same-family restoration, not a cross-server migration tool. SQLite behavior remains separate.

MariaDB's JSON alias is LONGTEXT with a JSON_VALID check. Reflection recognizes a direct ``json_valid(`column`)`` check from `information_schema.check_constraints` and gives that column JSON behavior. This preserves JSON null versus SQL NULL, clears required JSON to the JSON value null and excludes recognized JSON from string sampling. Arbitrary checks and unvalidated LONGTEXT remain text; review those columns explicitly. The check catalog is needed in addition to normal reflection permissions. See [MariaDB JSON documentation](https://mariadb.com/docs/server/reference/data-types/string-data-types/json).

Spatial values retain their binary representation and SRID through ST_GeomFromWKB export. MySQL exports explicitly select longitude/latitude axis order when rebuilding its internal WKB; MariaDB uses the two-argument form. They still require explicit masking review; this support does not mask coordinates. Generated columns are omitted from unmasked inserts and recomputed by the target; selected generated columns still block masked snapshots. ENUM/SET, application-only links and other existing masking limitations remain unchanged.

InnoDB remains required for consistent snapshots and transactional loads. Source transactions use repeatable-read, consistent-snapshot, read-only mode. Targets must be empty with matching schema and no triggers; loads restore foreign-key and SQL-mode session settings. See [loading](loading.md), [verification](verification.md) and [MariaDB transaction documentation](https://mariadb.com/docs/server/reference/sql-statements/transactions/start-transaction).

CI runs the shared integration suite and the full Sakila, Employees and synthetic framework round trips on both MariaDB series. Tests cover JSON object/null/SQL NULL, required JSON masking, spatial SRIDs, generated columns, mutual cycles, composite/null references, provider formats, consistent reads, transactional rollback and session settings. This does not cover every MariaDB storage engine, type, collation, server configuration or Galera deployment. Concurrent DDL remains unsupported.
