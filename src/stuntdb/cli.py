"""Command-line entry points."""
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

import typer

from stuntdb.core import IntegrityError, extract, reflect, sql_export
from stuntdb.schema import describe
from stuntdb.source import consistent_source, engine_for

app = typer.Typer(no_args_is_help=True)



@app.command()
def inspect(source: str):
    """Describe tables and declared foreign keys without printing row values."""
    try:
        engine = engine_for(source)
        with engine.connect() as connection:
            metadata = reflect(connection)
        typer.echo(json.dumps(describe(metadata), indent=2))
    except Exception:
        typer.echo("Inspection failed; check connectivity and schema permissions.", err=True)
        raise typer.Exit(1)


@app.command()
def snapshot(source: str, seed: str = typer.Option(...),
             output: Path = typer.Option(..., "--output", "-o"),
             allow_unmasked: bool = typer.Option(False), max_rows: int = 10000):
    """Extract seed rows and all declared parents, including cycles."""
    if not allow_unmasked:
        typer.echo("Masking is not implemented yet. Explicit --allow-unmasked is required.", err=True)
        raise typer.Exit(1)
    try:
        left, value = seed.split("=", 1)
        table, column = left.rsplit(".", 1)
        engine = engine_for(source)
        with consistent_source(engine) as connection:
            result = extract(connection, table, column, value, max_rows)
            sql = sql_export(result, engine.dialect)
        # Stage a complete export, then atomically replace the destination.
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", dir=output.parent, delete=False,
                                    encoding="utf-8") as handle:
                temporary = Path(handle.name)
                handle.write(sql)
            temporary.replace(output)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        typer.echo(f"Exported {sum(map(len, result.rows.values()))} unmasked rows.")
    except IntegrityError:
        typer.echo("Integrity check failed; no output written.", err=True)
        raise typer.Exit(3)
    except Exception:
        typer.echo("Snapshot failed; check seed, schema, row ceiling and connection.", err=True)
        raise typer.Exit(1)
