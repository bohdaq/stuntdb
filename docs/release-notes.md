# v0.3.0a1 release notes

This alpha checkpoint packages the existing extract → mask → load → verify workflow. No stable release, PyPI publication or outside-user validation is claimed.

## Available

- MySQL 8.0/8.4 and MariaDB 10.11/11.4 sources and targets; SQLite development fixtures.
- Cycle-safe parent closure, bounded child traversal, global row ceiling and temporary disk-backed key tracking.
- Default conservative HMAC masking, shared relationship domains, collision/unique checks and source-value leak detection.
- Reviewed JSON configuration, bounded local format suggestions, explicit email/phone/name/date/integer/decimal providers.
- Value-free export manifests, standalone file/database verification and transactional target loading.
- Versioned schema drift checks for defaults, generated expressions, collations, checks, indexes and declared relationships.
- Installed wheel/sdist quick-start coverage and published documentation.

The MariaDB compatibility tests also exposed and fixed a MySQL geographic-axis-order restoration bug. Fresh exports explicitly preserve longitude/latitude order when rebuilding MySQL's internal spatial WKB. Regenerate old exports containing geographic SRIDs rather than assuming their two-argument reconstruction preserves coordinates.

## Migration

Config `version` remains 1. Reviewed schema baselines now require `schema_version: 2`. Generate a new config file with `init`, compare the schema changes and transfer reviewed rules intentionally; existing configs are never overwritten automatically.

Manifests now use `version: 2` with a hashed-expression schema fingerprint. Legacy version-1 receipts remain usable for SQL checksum verification only. Database verification and loading require a newly generated snapshot. See [schema drift](schema-drift.md).

`init` samples by default (up to 100 rows per eligible table within global limits). Use `--sample-rows 0` for schema-only behavior. Uncertain findings become `review` and can block snapshots until resolved.

## Limits and remaining release work

Selected tables require primary keys. Seeds are single equality selections. Target schemas must already exist, match and be empty; trigger-bearing targets are rejected. All server base tables require InnoDB. No arbitrary SQL import or cross-family MySQL/MariaDB migration is supported. Concurrent DDL is unsupported.

Masking preserves storage formats, not application semantics; explicit kept values remain disclosed. Detector confidence is not calibrated. Free text and application-only links need review. Exports and restored rows still reside in memory. Schema protection is scoped rather than a complete DDL equivalence check.

Outside-user validation and a configured private vulnerability reporting channel remain required before a stable public release. See [validation](outside-user-validation.md), [security](security.md) and the [release checklist](releasing.md).
