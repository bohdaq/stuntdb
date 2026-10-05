# stuntdb

Build small, masked database fixtures for development while retaining declared foreign-key relationships, including cycles.

**v0.3.0** Supported servers are MySQL 8.0/8.4 and MariaDB 10.11/11.4. SQLite fixtures let you try the workflow without a database server.

Start with the [synthetic quick-start demo](quickstart.md), which exercises inspect → init → snapshot → load → verify using installed packages. Then review [configuration](configuration.md) and [masking limits](masking.md) before working with real data.

Snapshots mask by default, use a project secret salt, and write an SQL export plus a value-free manifest. Parent closure follows declared references to a fixed point. Bounded child traversal selects dependents without expanding unrelated parent rows. [Loading](loading.md) requires an empty matching target schema and commits only after row-count and foreign-key checks pass.

This tool does not infer every application relationship, create schemas or guarantee absence of personal data. Review sampling suggestions and masking rules, use read-only source credentials, and keep real dumps and secrets private. Outside-user validation was [waived by the owner](outside-user-validation.md) for this release and has not been performed.

Source and Apache-2.0 license: [GitHub repository](https://github.com/bohdaq/stuntdb).
