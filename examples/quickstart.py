#!/usr/bin/env python3
"""Run the installed CLI against fresh, synthetic SQLite development fixtures."""
import argparse
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import os

DDL = '''
CREATE TABLE customers (
 id INTEGER PRIMARY KEY, name VARCHAR(100), email VARCHAR(100), phone VARCHAR(30),
 birth_date DATE, notes TEXT, preferred_order_id INTEGER REFERENCES orders(id));
CREATE TABLE orders (
 id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id), amount NUMERIC(8,2));
CREATE TABLE line_items (
 id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id), description VARCHAR(100));
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, help='Create this new directory; existing paths are refused')
    args = parser.parse_args()
    command = shutil.which('stuntdb')
    if command is None:
        parser.error('Install stuntdb in the active environment first')
    if args.directory:
        directory = args.directory.resolve()
        directory.mkdir(parents=True, exist_ok=False)
    else:
        directory = Path(tempfile.mkdtemp(prefix='stuntdb-demo-')).resolve()
    source, target = directory / 'source.db', directory / 'target.db'
    for database in (source, target):
        with sqlite3.connect(database) as c:
            c.executescript(DDL)
    with sqlite3.connect(source) as c:
        for i, name in enumerate(('Alice Example', 'Bob Example', 'Carol Example'), 1):
            c.execute('INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)',
                      (i, name, f'canary{i}@example.org', f'+48 (123) 456-78{i}',
                       f'196{i}-02-01', f'private synthetic notes {i}', 100 + i))
            c.execute('INSERT INTO orders VALUES (?, ?, ?)', (100 + i, i, '123.45'))
        c.executemany('INSERT INTO line_items VALUES (?, ?, ?)',
                      [(1, 101, 'Synthetic item one'), (2, 101, 'Synthetic item two'), (3, 102, 'Unselected item')])
    env = dict(os.environ, STUNTDB_SALT='demo-only-salt-never-use-with-real-data')
    source_url, target_url = f'sqlite:///{source}', f'sqlite:///{target}'
    config, export = directory / 'stuntdb.json', directory / 'slice.sql'
    manifest = Path(str(export) + '.manifest.json')
    def run(*options):
        subprocess.run([command, *map(str, options)], check=True, env=env)
    run('inspect', source_url)
    run('init', source_url, '-o', config)
    settings = json.loads(config.read_text())
    # The synthetic fixture has known formats; unexpected review is a demo failure.
    assert 'review' not in settings['rules'].values()
    run('snapshot', source_url, '--config', config, '--seed', 'orders.id=101', '--children', '1', '-o', export)
    run('verify', export, '--manifest', manifest)
    run('load', export, '--target', target_url)
    run('verify', target_url, '--manifest', manifest, '--reference-source', source_url,
        '--seed', 'orders.id=101', '--children', '1')
    with sqlite3.connect(target) as c:
        assert c.execute('SELECT COUNT(*) FROM customers').fetchone()[0] == 1
        assert c.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 1
        assert c.execute('SELECT COUNT(*) FROM line_items').fetchone()[0] == 2
        assert c.execute('SELECT email FROM customers').fetchone()[0].endswith('@example.invalid')
        assert c.execute('SELECT notes FROM customers').fetchone()[0] is None
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
    print(f'Demo passed: 4 masked rows, cyclic references intact. Artifacts: {directory}')


if __name__ == '__main__':
    main()
