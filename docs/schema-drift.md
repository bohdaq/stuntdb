# Schema drift protection

New `init` configurations carry `schema_version: 2`; new snapshot manifests use manifest `version: 2` and include the schema fingerprint as well as its SHA-256 hash. Snapshot compares a reviewed baseline to the source. Load and database verification compare the exported fingerprint to the target, before inserting rows and again before committing a load.

The fingerprint covers:

- Column type, nullability, integer signedness, enum/SET members, primary-key membership and ordered primary-key columns.
- Column character sets and collations, server defaults, generated expressions and stored/virtual generation.
- Declared foreign-key columns and targets, update/delete actions and reflected deferral options.
- Index columns and order, uniqueness, prefix lengths, index type and MySQL functional-index expressions.
- Check expressions and MySQL enforcement status, including MariaDB inline JSON validation checks.
- Base-table engine and default collation on MySQL/MariaDB.

Expression and default literals are stored as hashes. Mismatch output lists paths such as `person.columns.email.collation: changed` or `person.indexes: changed`, without printing the underlying default/check values. Up to twelve changed paths are displayed. Match the target schema to the export; if a source change is intentional, regenerate a config to a new path, review and transfer the masking rules, then create a new snapshot. Existing reviewed configs are never overwritten automatically.

Default, generated and constraint expression text is compared conservatively. Semantically equivalent spelling can produce a mismatch; the guard does not parse SQL equivalence. MySQL/MariaDB catalog fields supplement SQLAlchemy reflection, including inline MariaDB CHECK constraints and unique prefix indexes. Metadata catalog permissions are required. Constraint/index names, index cardinality/statistics and changing auto-increment counters are excluded so data growth or renamed indexes do not themselves invalidate matching schemas.

Legacy schema baselines without `schema_version: 2` require regeneration with `init`. Their existing reviewed rules can be transferred after review; no automatic migration or weaker fallback is applied. Legacy version-1 manifests remain usable for SQL checksum verification only. Loading or database verification requires a new snapshot and version-2 receipt. Config format `version` remains 1; it is separate from the schema fingerprint version.

This remains a scoped guard. Triggers, views, routines, partitioning, row formats, index visibility/parser options, custom spatial-reference definitions and every server-specific DDL option are outside the fingerprint. Trigger-bearing targets remain rejected by the loader. SQLite coverage is limited to reflected schema properties and does not recover all collations or index expression details. Same server-family matching is required; this is not a schema migration tool. Concurrent DDL during extraction/loading remains unsupported.

Schema drift exits with code 3 and leaves snapshot output untouched or rolls back a load. Diagnostics identify what changed so the user can align schemas or intentionally regenerate; they do not modify the database schema.
