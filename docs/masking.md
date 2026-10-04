# Masking checkpoint

Snapshots mask by default. Supply a project secret using `STUNTDB_SALT` (or choose an environment-variable name with `--salt-env`). The secret must contain at least 16 bytes. Keep it stable for repeatable outputs and keep it private; a leaked salt enables guessing low-entropy source values. Do not commit the secret. `--allow-unmasked` explicitly bypasses masking and leak checks.

This initial implementation is deliberately conservative:

- VARCHAR/CHAR and string keys receive HMAC-SHA256 hex tokens, bounded by the shortest column length in their relationship domain. Same source value, schema domain and salt give the same token. Unrelated columns have separate domains. Tokens do not preserve email, phone or name formatting yet.
- Text, JSON and binary data are cleared. Nullable columns become SQL NULL; required text/binary become empty; required JSON becomes JSON null. Original JSON null and SQL NULL remain distinct on the unmasked path.
- Ordinary numbers, booleans and dates are retained. Column-name heuristics reject sensitive non-text fields (for example numeric phone or birth dates); these heuristics are not exhaustive PII detection.
- Enum/SET, spatial, computed and unknown types fail closed for selected tables. Configurable reviewed rules, sample-based detection and type-specific providers remain pending.

Relationship columns share a masking domain. Foreign-key integrity and primary/unique constraints are checked after masking. Token collisions fail the run rather than choosing a data-dependent alternative that could change across subsets. Very narrow columns may therefore need future reviewed rules.

The leak check compares all source strings from transformed columns against all output strings, including nested JSON strings and binary hex representations. It detects equal values and source strings of at least eight characters embedded in output text. It is limited to extracted rows and values selected for transformation, not all source database values. Numbers/dates deliberately retained by this checkpoint are outside the leak check; it does not prove absence of all personal data.

Every validation finishes before serialization or file replacement. Leak failures exit 2, integrity failures exit 3; unsupported masking/schema cases exit 1. Errors contain no row values or salt. Existing output remains intact on a failed run. An in-memory flag distinguishes masked from unmasked export headers; the flag alone is not independent verification of an exported file.

Integration tests restore a masked natural email key into MySQL and verify the join and cleared fields. Local tests cover determinism, alternate salts, shared domains, unique/collision failure, Unicode, canary leakage, generated-column refusal and atomic CLI failures. Full schema-zoo runs continue to use the explicit unmasked path until reviewed rules support their enum and spatial fields.
