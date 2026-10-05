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
from stuntdb.manifest import build_manifest, load_manifest, verify_file, verify_database
from stuntdb.masking import check_leaks
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
             salt_env: str | None = None, config: Path | None = None,
             manifest: Path | None = None):
    """Extract seed rows and all declared parents, including cycles."""
    try:
        manifest = manifest or Path(str(output) + '.manifest.json')
        if output.resolve() == manifest.resolve():
            raise ValueError('Export and manifest paths must differ')
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
            receipt = build_manifest(result, sql, engine.dialect.name, {
                'config': settings, 'seed': seed, 'children': children,
                'max_rows': max_rows, 'allow_unmasked': allow_unmasked})
        # Stage a complete export, then atomically replace the destination.
        temporary = None
        receipt_temporary = None
        try:
            with NamedTemporaryFile(mode="w", dir=output.parent, delete=False,
                                    encoding="utf-8") as handle:
                temporary = Path(handle.name)
                handle.write(sql)
            with NamedTemporaryFile(mode='w', dir=manifest.parent, delete=False,
                                    encoding='utf-8') as handle:
                receipt_temporary = Path(handle.name)
                json.dump(receipt, handle, indent=2, sort_keys=True)
                handle.write('\n')
            receipt_temporary.replace(manifest)
            temporary.replace(output)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if receipt_temporary is not None:
                receipt_temporary.unlink(missing_ok=True)
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


@app.command('verify')
def verify_command(target: str, manifest: Path = typer.Option(...),
                   reference_source: str | None = None, seed: str | None = None,
                   max_rows: int = typer.Option(10000, min=1),
                   children: int = typer.Option(0, min=0)):
    """Check an export checksum or a restored database; optionally compare source leaks."""
    try:
        receipt = load_manifest(manifest)
        if '://' not in target:
            if reference_source or seed:
                raise ValueError('Leak comparison requires a database target')
            verify_file(Path(target), receipt)
            typer.echo('Export checksum verified. SQL was not executed; database integrity and leaks were not rechecked.')
            return
        if bool(reference_source) != bool(seed):
            raise ValueError('Reference source and seed must be supplied together')
        with consistent_source(engine_for(target)) as connection:
            result = verify_database(connection, receipt)
        if reference_source:
            if not receipt['masked']:
                raise ValueError('Leak comparison requires a masked export')
            left, value = seed.split('=', 1)
            table, column = left.rsplit('.', 1)
            with consistent_source(engine_for(reference_source)) as connection:
                original = extract(connection, table, column, value, max_rows, children)
            if not any(original.rows.values()):
                raise ValueError('Reference seed selected no rows')
            from stuntdb.config import schema_signature
            if schema_signature(original.metadata) != schema_signature(result.metadata):
                raise IntegrityError('Reference schema differs')
            columns = [label.rsplit('.', 1) for label, action in receipt['strategies'].items() if action != 'keep']
            check_leaks(original, result, columns)
            typer.echo('Schema, row counts, foreign keys and source-value leak check passed.')
        else:
            typer.echo('Schema, row counts and foreign keys verified. Leak check requires --reference-source and --seed.')
    except LeakError:
        typer.echo('Leak check failed.', err=True)
        raise typer.Exit(2)
    except IntegrityError:
        typer.echo('Verification failed: checksum, schema, row counts or foreign keys differ.', err=True)
        raise typer.Exit(3)
    except Exception:
        typer.echo('Verification failed; check manifest, target and reference options.', err=True)
        raise typer.Exit(1)


@app.command()
def init(source: str, output: Path = typer.Option(Path('stuntdb.json'), '--output', '-o'),
         sample_rows: int = typer.Option(100, min=0, max=1000)):
    """Write reviewed masking suggestions; --sample-rows 0 reads only schema."""
    try:
        engine = engine_for(source)
        with consistent_source(engine) as connection:
            metadata = reflect(connection)
            settings = generated_config(metadata)
            if sample_rows:
                from stuntdb.detection import sample_suggestions
                settings['detection'] = sample_suggestions(connection, metadata, settings, sample_rows)
        content = json.dumps(settings, indent=2, sort_keys=True) + '\n'
        # Exclusive create protects previously reviewed rules from overwriting.
        with output.open('x', encoding='utf-8') as handle:
            handle.write(content)
        typer.echo('Wrote value-free config. Review rules and set a seed before snapshotting.')
    except Exception:
        typer.echo('Init failed; check source permissions or use a new output path.', err=True)
        raise typer.Exit(1)
