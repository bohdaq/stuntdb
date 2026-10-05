# stuntdb

Cycle-safe database slices for development. Apache-2.0 licensed.

This is pre-release software with an initial conservative masking implementation. MySQL 8 is the initial target; MariaDB is scheduled for v0.3. SQLite is available for local development and tests.

```sh
# Set STUNTDB_SALT to a stable project secret before snapshots.
python -m pip install -e '.[dev]'
stuntdb inspect 'mysql+pymysql://user:password@localhost/database'
stuntdb snapshot 'mysql+pymysql://user:password@localhost/database' \
  --seed 'orders.id=123' --children 1 -o seed.sql
pytest
```

`inspect` reports declared relationship cycles without reading row values. Snapshots follow every declared parent reference to a fixed point, including cycles, self-references and composite keys. Use `--children N` to follow declared dependents N levels from the seed (default 0). Rows added only to satisfy parent references do not expand into unrelated children. A global row ceiling stops oversized slices. Traversal keys use a private temporary SQLite store that is removed after each run; selected rows and the export still reside in memory. Integrity is checked before an atomic SQL export. MySQL reads use a read-only consistent snapshot and reject non-InnoDB tables and unsupported server versions; use a read-only account. Concurrent schema changes during extraction are unsupported. Exports contain inserts for an existing matching schema and temporarily disable foreign-key checks to load cycles. Use an empty development database; disabling checks does not validate existing target rows.

**Masking is now the default.** Provide a secret through `STUNTDB_SALT`. Strings receive deterministic tokens, linked columns share domains, and text/JSON/binary fields are cleared. Unsupported sensitive types fail closed. Leak and integrity checks run before output. Explicit `--allow-unmasked` exports original data. Use `stuntdb init`, [reviewed config rules](docs/configuration.md), and [format-preserving providers](docs/providers.md) for supported personal-data fields. Read the [masking scope and limits](docs/masking.md) before using it with real data.

Current limits: selected tables require primary keys; equality seeds only; no schema creation, direct target loading, sample-based PII detection, manifest, or standalone verification. JSON, binary and UTF-8 strings are exported with SQL-mode-independent hex literals; this encoding is reversible; privacy comes from masking, not SQL encoding. Other literal support depends on column types; unsupported values fail before export. The extraction foundation passes MySQL 8.0/8.4 integration CI; the schema zoo covers full Sakila and Employees imports plus synthetic framework shapes. Application-only links, triggers, routines and views are outside the round-trip target. See [testing](docs/testing.md).

See [roadmap](docs/roadmap.md) and [security reporting](SECURITY.md).

Snapshots include a value-free manifest. Check an export or restored database with `stuntdb verify TARGET --manifest slice.sql.manifest.json`; see [verification scope and source-value checks](docs/verification.md).
