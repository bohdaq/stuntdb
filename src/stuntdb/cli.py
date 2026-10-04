"""Command-line entry points."""
import os
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

import typer

from stuntdb.core import IntegrityError, extract, reflect, sql_export
from stuntdb.masking import mask_slice, LeakError, MaskingError
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
             allow_unmasked: bool = typer.Option(False), max_rows: int = 10000,
             children: int = typer.Option(0, min=0),
             salt_env: str = "STUNTDB_SALT"):
    """Extract seed rows and all declared parents, including cycles."""
    salt = os.environ.get(salt_env, "")
    if not allow_unmasked and len(salt.encode()) < 16:
        typer.echo("Set the salt environment variable to a secret of at least 16 bytes.", err=True)
        raise typer.Exit(1)
    try:
        left, value = seed.split("=", 1)
        table, column = left.rsplit(".", 1)
        engine = engine_for(source)
        with consistent_source(engine) as connection:
            result = extract(connection, table, column, value, max_rows, children)
            if not allow_unmasked:
                result = mask_slice(result, salt)
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
        status = "masked" if result.masked else "unmasked"
        typer.echo(f"Exported {sum(map(len, result.rows.values()))} {status} rows.")
    except LeakError:
        typer.echo("Leak check failed; no output written.", err=True)
        raise typer.Exit(2)
    except MaskingError:
        typer.echo("Masking failed; check salt and unsupported schema types. No output written.", err=True)
        raise typer.Exit(1)
    except IntegrityError:
        typer.echo("Integrity check failed; no output written.", err=True)
        raise typer.Exit(3)
    except Exception:
        typer.echo("Snapshot failed; check seed, schema, row ceiling and connection.", err=True)
        raise typer.Exit(1)
