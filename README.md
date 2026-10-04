# stuntdb

Cycle-safe database slices for development. Apache-2.0 licensed.

This is an early v0.1 foundation, not a production-ready masking tool. MySQL 8 is the initial target; MariaDB is scheduled for v0.3. SQLite is available for local development and tests.

```sh
python -m pip install -e '.[dev]'
stuntdb inspect 'mysql+pymysql://user:password@localhost/database'
stuntdb snapshot 'mysql+pymysql://user:password@localhost/database' \
  --seed 'orders.id=123' --children 1 --allow-unmasked -o seed.sql
pytest
```

`inspect` reports declared relationship cycles without reading row values. Snapshots follow every declared parent reference to a fixed point, including cycles, self-references and composite keys. Use `--children N` to follow declared dependents N levels from the seed (default 0). Rows added only to satisfy parent references do not expand into unrelated children. A global row ceiling stops oversized slices. Integrity is checked before an atomic SQL export. MySQL reads use a read-only consistent snapshot and reject non-InnoDB tables and unsupported server versions; use a read-only account. Concurrent schema changes during extraction are unsupported. Exports contain inserts for an existing matching schema and temporarily disable foreign-key checks to load cycles. Use an empty development database; disabling checks does not validate existing target rows.

**Exports currently contain original data.** Snapshot requires `--allow-unmasked` until deterministic masking and leak checks ship. Do not use sensitive production data. Command errors omit driver diagnostics to avoid disclosing credentials or row values.

Current limits: selected tables require primary keys; equality seeds only; no schema creation, direct target loading, PII detection, masking, manifest, or leak check. SQL literal support depends on column types; unsupported values fail before export. MySQL 8.0/8.4 integration jobs are configured but have not yet been run here; the full schema zoo remains pending. See [testing](docs/testing.md).

See [roadmap](docs/roadmap.md) and [security reporting](SECURITY.md).
