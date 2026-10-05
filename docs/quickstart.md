# Quick start

Use Python 3.10 or newer and a fresh virtual environment. Install the tagged release from the repository using the commands below. PyPI publication is a separate step.

```sh
git clone --branch v0.3.0 https://github.com/bohdaq/stuntdb.git
cd stuntdb
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python examples/quickstart.py
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell. The demo is Python and requires no Docker or external database server.

The script creates synthetic source and target databases in a new temporary directory, uses a clearly marked demo-only salt, runs the installed CLI, and leaves its config, SQL, manifest and databases for inspection. It refuses to reuse an existing directory when `--directory PATH` is supplied. It never connects to user databases.

Expected final output:

```text
Demo passed: 4 masked rows, cyclic references intact. Artifacts: …
```

The source has three customers and orders. The seed selects order 101, parent closure keeps its customer and mutual preferred-order reference, and `--children 1` includes two line items. The target has one customer, one order and two line items. The demo checks synthetic email output, cleared notes and SQLite foreign-key integrity, then reruns source-value leak detection through `verify`.

For your own database, privately set source/target URLs and a stable project salt of at least 16 bytes. Use a read-only source account. Prepare an empty matching development schema yourself, then:

```sh
stuntdb init "$STUNTDB_SOURCE" -o stuntdb.json
# Review every rule before proceeding. Replace unresolved review actions.
stuntdb snapshot --config stuntdb.json --seed orders.id=42 --children 1 -o slice.sql
stuntdb verify slice.sql --manifest slice.sql.manifest.json
stuntdb load slice.sql --target "$STUNTDB_TARGET"
stuntdb verify "$STUNTDB_TARGET" --manifest slice.sql.manifest.json \
  --reference-source "$STUNTDB_SOURCE" --seed orders.id=42 --children 1
```

Do not reuse the demo salt with real data. `review` blocks selected unsupported or uncertain columns. Explicit `keep` decisions retain source values. A leak check is scoped to transformed columns and is not an anonymization guarantee. [Configuration](configuration.md), [loading](loading.md) and [verification](verification.md) explain the remaining limits.
