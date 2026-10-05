# Release checklist

The current version is `0.3.0`. The owner waived independent validation in issue #2 on 2026-10-05; no outside-user result is claimed. Private vulnerability reporting is enabled.

## Reproduce checks

```sh
python -m pip install -e '.[dev,docs,release]'
python -m pytest -q
python scripts/package_smoke.py
python -m twine check dist/stuntdb-0.3.0*
python -m mkdocs build --strict
```

The smoke script selects the current version's wheel and sdist, installs each in a clean environment, checks dependency metadata/version agreement and runs the complete synthetic workflow outside the checkout. Older artifacts are preserved. CI also covers Python 3.10, 3.12 and 3.14 and all four supported server series.

## GitHub release

Synchronize package/runtime versions and release notes. Push main and require the Tests workflow to pass on that exact commit. Then push `vVERSION`. The release workflow checks the successful main CI run, validates the tag/version, builds and smoke-tests packages, checks metadata, creates SHA-256 checksums and publishes the GitHub release assets.

## PyPI publication

Configure a pending trusted publisher at PyPI for project `stuntdb`, owner `bohdaq`, repository `stuntdb`, workflow `publish.yml`, environment `pypi`. This requires the owner's PyPI account. See [PyPI's setup instructions](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).

After configuration, manually run **Publish to PyPI** with the existing release tag (for example `v0.3.0`). It downloads the GitHub release assets, verifies their checksums and uploads those same artifacts using short-lived trusted-publishing credentials. The GitHub release does not imply PyPI publication.

## Docs deployment

Pull requests build docs in strict mode. Main pushes deploy through GitHub Pages Actions. Review [migration notes](release-notes.md), [security](security.md) and [validation status](outside-user-validation.md) before each release.
