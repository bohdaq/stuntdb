# Implementation roadmap

Decisions: project and CLI name `stuntdb`; Apache-2.0; MariaDB support in v0.3.

The supplied design is planning context. Competitor evaluations and adoption claims have not been independently verified. The proposed dbslice / Greenmask / subsetter / Jailer comparison gate remains pending; no benchmark result is implied by this repository.

- v0.1: MySQL 8 snapshot and inspect; cycle-safe parent closure; SQL output; integrity checks; MySQL 8.0/8.4 integration matrix and schema zoo. Foundation, cycle reporting and bounded child traversal implemented; MySQL 8.0/8.4 integration matrix configured, execution unverified; full zoo pending.
- v0.2: deterministic HMAC masking, shared key domains, collision checks, PII detection, leak checks, manifest, init and verify; canary and property tests.
- v0.3: target loading, declared relationships, schema drift guard, docs site, MariaDB integration matrix and outside-user validation.
- v0.4: evaluate MCP access, optional local free-text model and synthetic generation after feedback.

Until v0.2, exports require explicit unmasked opt-in. No privacy or launch-readiness claims are made.
