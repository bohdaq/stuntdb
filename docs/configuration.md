# Reviewed configuration

`stuntdb init SOURCE -o stuntdb.json` reflects the schema without reading row values. It writes a JSON config containing a schema baseline and one rule per column. It never writes the source URL, credentials or salt; those are referenced by environment-variable names. Existing files are not overwritten.

```sh
# Set STUNTDB_SOURCE and STUNTDB_SALT privately in your shell or secret manager.
stuntdb init "$STUNTDB_SOURCE" -o stuntdb.json
# Review the generated rules, then:
stuntdb snapshot --config stuntdb.json --seed 'orders.id=123' -o seed.sql
```

The config supports `version` (1), `source_env`, `salt_env`, `seed` (one equality seed), `children`, `max_rows`, `schema`, and `rules`. Explicit command-line source, seed and traversal/salt settings override their config equivalents. Source defaults to STUNTDB_SOURCE; salt defaults to STUNTDB_SALT. Unknown fields, duplicate keys, unsupported actions and invalid bounds fail before output.

Rule actions:

| Action | Behavior |
| --- | --- |
| auto | Use the conservative built-in policy |
| review | Block snapshots when the column's table or relationship domain is selected |
| keep | Explicitly retain the source value; it is excluded from the source-value set being checked for transformation |
| clear | Use SQL NULL for nullable columns; empty text/binary or JSON null for supported required fields |
| token | HMAC token for ordinary strings, including text |

An explicit action applies to the entire linked column domain, including columns whose rule is auto. Conflicting explicit actions fail. Clearing primary or relationship keys is forbidden. Required numeric/date/spatial fields cannot be cleared; nullable scalar and enum fields can be. Enum/SET tokenization and computed-column rules remain unsupported; computed columns still fail even with keep because their target values may be recalculated from masked inputs.

Generated configs mark sensitive non-text and unsupported columns as review, never automatically keep. Changing review to keep is an intentional disclosure decision. The schema-zoo tests use explicit sample-only keep rules for enum/SET, spatial fields and birth dates; those tests do not establish complete anonymization of the datasets.

A generated baseline records column types, nullability, primary-key membership, computed/spatial flags and declared foreign keys. Changes to those fields or added/removed columns/tables fail the snapshot until you regenerate to a new file and review the changes. Defaults, triggers, routines, collations and every index option are not a complete schema fingerprint. Unknown rule column names fail even without a baseline. Handwritten configs may omit the baseline; doing so disables this drift check.

The leak check remains active for transformed columns and scans kept columns too: copying a transformed source value into an explicitly kept column still fails. Direct scalar values from cleared numeric/date columns are checked in addition to strings. Low-entropy values may cause conservative failures if the same value remains elsewhere. Retained values are not privacy-protected merely because other columns are masked.

JSON is used for this checkpoint to keep parsing strict and dependency-free. Multiple seeds, SQL predicates, YAML, format-preserving providers, manifests and standalone verification remain pending.
