"""Command-line entry points."""
import os
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

import typer

from stuntdb.config import generated_config, load_config, validate_schema
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
def snapshot(source: str | None = typer.Argument(None), seed: str | None = typer.Option(None),
             output: Path = typer.Option(..., "--output", "-o"),
             allow_unmasked: bool = typer.Option(False), max_rows: int | None = None,
             children: int | None = typer.Option(None, min=0),
             salt_env: str | None = None, config: Path | None = None):
    """Extract seed rows and all declared parents, including cycles."""
    try:
        settings = load_config(config) if config else {}
        source = source or os.environ.get(settings.get('source_env', 'STUNTDB_SOURCE'))
        seed = seed if seed is not None else settings.get('seed')
        if not source or not seed:
            raise ValueError('Source and seed required')
        max_rows = max_rows if max_rows is not None else settings.get('max_rows', 10000)
        children = children if children is not None else settings.get('children', 0)
        salt = os.environ.get(salt_env or settings.get('salt_env', 'STUNTDB_SALT'), '')
        if not allow_unmasked and len(salt.encode()) < 16:
            raise MaskingError('Secret salt must contain at least 16 bytes')
        left, value = seed.split("=", 1)
        table, column = left.rsplit(".", 1)
        engine = engine_for(source)
        with consistent_source(engine) as connection:
            result = extract(connection, table, column, value, max_rows, children)
            validate_schema(settings, result.metadata)
            if not allow_unmasked:
                result = mask_slice(result, salt, settings.get('rules', {}))
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


@app.command()
def init(source: str, output: Path = typer.Option(Path('stuntdb.json'), '--output', '-o')):
    """Write a schema-only masking config for review; never store the source URL."""
    try:
        engine = engine_for(source)
        with engine.connect() as connection:
            settings = generated_config(reflect(connection))
        content = json.dumps(settings, indent=2, sort_keys=True) + '\n'
        # Exclusive create protects previously reviewed rules from overwriting.
        with output.open('x', encoding='utf-8') as handle:
            handle.write(content)
        typer.echo('Wrote schema-only config. Review rules and set a seed before snapshotting.')
    except Exception:
        typer.echo('Init failed; check source permissions or use a new output path.', err=True)
        raise typer.Exit(1)
