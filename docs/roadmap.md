# Implementation roadmap

Decisions: project and CLI name `stuntdb`; Apache-2.0; MariaDB support in v0.3.

The supplied design is planning context. Competitor evaluations and adoption claims have not been independently verified. The proposed dbslice / Greenmask / subsetter / Jailer comparison gate remains pending; no benchmark result is implied by this repository.

- v0.1: MySQL 8 snapshot and inspect; cycle-safe parent closure; SQL output; integrity checks; MySQL 8.0/8.4 integration matrix and schema zoo. Foundation, cycle reporting and bounded child traversal implemented; MySQL 8.0/8.4 integration matrix passing for the extraction foundation; disk-backed key tracking and graph property tests implemented; full Sakila/Employees import and round-trip jobs added; synthetic framework shapes test declared references and explicitly document application-only links. Real framework installations remain future coverage.
- v0.2: conservative HMAC string masking, shared key domains, collision/unique checks and extracted-value leak checks implemented with canary and Unicode property tests. Reviewed JSON rules, schema-only init, a scoped schema baseline, scalar leak checks and configured masked sample round trips implemented. Explicit email/phone/name/date/integer/decimal providers implemented, and the masked zoo uses birth-date providers. Value-free export manifests and standalone checksum/database/source-value verification implemented. Sample-based detection remains pending.
- v0.3: target loading, declared relationships, schema drift guard, docs site, MariaDB integration matrix and outside-user validation.
- v0.4: evaluate MCP access, optional local free-text model and synthetic generation after feedback.

Snapshots now require a secret environment salt and mask by default. Original-data exports require explicit unmasked opt-in. See the masking scope documentation; no general privacy or launch-readiness claim is made.
