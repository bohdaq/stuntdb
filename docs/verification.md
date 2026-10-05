# Export manifests and verification

Every snapshot writes a JSON receipt beside its SQL file (`slice.sql.manifest.json`). Use `--manifest PATH` to choose another path. The receipt includes tool version, dialect, per-table row counts, resolved masking actions, a versioned scoped schema fingerprint and hash, an effective config hash and the SQL SHA-256 checksum. It contains no source URL, salt, seed text or row values. Column/table names are schema information. Hashes are receipts, not signatures or proof of anonymization; keep manifests with their exports.

```sh
stuntdb snapshot --config stuntdb.json --seed orders.id=42 -o slice.sql
stuntdb verify slice.sql --manifest slice.sql.manifest.json
stuntdb verify "$STUNTDB_TARGET" --manifest slice.sql.manifest.json
stuntdb verify "$STUNTDB_TARGET" --manifest slice.sql.manifest.json \
  --reference-source "$STUNTDB_SOURCE" --seed orders.id=42 --children 0
```

File verification streams the checksum and never executes the SQL. A matching file retains the snapshot's original checks; it does not rerun database checks or leak detection. A changed export fails with exit code 3.

Database verification reads a consistent snapshot, compares the dialect, scoped schema hash and exact table counts, then checks every declared foreign key, including composite references and cycles. It expects an otherwise empty matching schema populated by the export; additional rows fail. It does not load SQL or alter either database. Verification currently materializes the target rows in memory. The [version-2 schema fingerprint](schema-drift.md) covers column/default/generated details, collations, checks, indexes and declared relationships; triggers, views and all server-specific DDL remain outside it. This is not a byte-for-byte comparison of restored values.

To rerun leak detection, supply a reference source and the original seed and traversal bounds. The checker extracts that source slice and scans all target values for originals from columns the manifest marks as transformed. No salt is needed. An empty seed selection fails. Source changes since export change what this comparison can establish; use a stable reference snapshot. Detection has the same scoped equality/substring/scalar limitations as snapshot masking, and explicitly kept columns are excluded from its source set. It cannot establish absence of arbitrary personal data.

Exit codes: 0 passed requested checks; 1 invalid options/manifest or connection failure; 2 source-value leak; 3 checksum, schema, count or foreign-key failure. Output states when a leak check was skipped.

Both output files are staged before publication, with private temporary files. Each destination replacement is atomic, but the pair cannot be replaced atomically across two paths. An interruption between replacements can leave mismatched files; checksum verification detects this. Keep destinations separate from source databases and reviewed configuration files.
