#!/usr/bin/env python3
"""Install wheel and sdist into isolated environments, then run the real demo."""
from pathlib import Path
import os
import runpy
import subprocess
import sys
import tempfile
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    subprocess.run([sys.executable, '-m', 'build', str(ROOT)], check=True)
    version = runpy.run_path(str(ROOT / 'src/stuntdb/__init__.py'))['__version__']
    wheel = list((ROOT / 'dist').glob(f'stuntdb-{version}-*.whl'))
    sdist = list((ROOT / 'dist').glob(f'stuntdb-{version}.tar.gz'))
    if len(wheel) != 1 or len(sdist) != 1:
        raise RuntimeError('Expected one wheel and sdist for the current version')
    for artifact in (wheel[0], sdist[0]):
        with tempfile.TemporaryDirectory(prefix='stuntdb-package-') as tmp:
            directory = Path(tmp)
            environment = directory / 'venv'
            venv.EnvBuilder(with_pip=True).create(environment)
            binaries = environment / ('Scripts' if os.name == 'nt' else 'bin')
            python = binaries / ('python.exe' if os.name == 'nt' else 'python')
            env = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ['PATH'])
            env.pop('PYTHONPATH', None)
            subprocess.run([str(python), '-m', 'pip', 'install', str(artifact)], check=True, cwd=directory, env=env)
            subprocess.run([str(python), '-m', 'pip', 'check'], check=True, cwd=directory, env=env)
            subprocess.run([str(python), '-c', "import stuntdb; from importlib.metadata import version; assert stuntdb.__version__ == version('stuntdb'); print(version('stuntdb'))"], check=True, cwd=directory, env=env)
            subprocess.run([str(binaries / ('stuntdb.exe' if os.name == 'nt' else 'stuntdb')), '--help'], check=True, cwd=directory, env=env)
            subprocess.run([str(python), str(ROOT / 'examples/quickstart.py'), '--directory', str(directory / 'demo')], check=True, cwd=directory, env=env)
    print('Wheel and sdist installation smoke tests passed.')


if __name__ == '__main__':
    main()
