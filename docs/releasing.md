# Release checklist

The current package checkpoint is `0.3.0a1`. Preparing artifacts does not publish them. A stable release remains gated on independent validation and a configured private security-reporting route.

## Reproduce checks

```sh
python -m pip install -e '.[dev,docs,release]'
python -m pytest -q
python scripts/package_smoke.py
python -m twine check dist/*
python -m mkdocs build --strict
```

Start with an empty `dist/` directory; the smoke script refuses ambiguous wheel/sdist selections. It builds source and wheel distributions, installs each in its own clean environment, checks dependency metadata and version agreement, invokes the installed CLI from outside the checkout, and runs the complete synthetic workflow. It neither uploads packages nor deletes existing release artifacts.

CI repeats these checks across Python 3.10, 3.12 and 3.14 and runs integrations/zoo against all four supported server series. Require all relevant jobs to pass on the exact commit being released. Inspect release artifact metadata and license inclusion before publishing.

## Before a stable release

- Complete and record [outside-user validation](outside-user-validation.md); resolve release-blocking feedback.
- Configure a private vulnerability reporting route and update the security policy with the verified channel.
- Review schema-version migration notes and masking/load limits.
- Confirm the docs site and clean-install instructions are usable.
- Select and synchronize package and runtime version; build and verify fresh artifacts.
- Create a reviewed release/tag and publish artifacts only when explicitly requested. PyPI credentials or trusted-publishing setup are not part of this checkpoint.

## Docs deployment

Pull requests build the docs in strict mode. Main pushes deploy the built static site through the GitHub Pages Actions environment. Pages must be configured to use GitHub Actions as its publishing source. Deployment uses Pages/id-token permissions; no package publishing credential is involved.
