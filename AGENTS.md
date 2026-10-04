# Repository workflow

The owner works solo and prefers committing and pushing directly to `main`.
Do not create a pull request unless the owner asks for one. Existing pull requests
may be merged automatically after their relevant checks pass.

Use `stuntdb` as the project and CLI name, Apache-2.0 as the license, and keep
MariaDB support scheduled for v0.3.

Run relevant tests before pushing. MySQL integration tests require a disposable
`stuntdb_test` database; never run their destructive fixtures against user data.
