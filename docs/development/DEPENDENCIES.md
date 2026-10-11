# Dependency and Lock Strategy

## Authority

`pyproject.toml` is the authoritative declaration of direct runtime and development dependencies. Direct requirements are bounded to prevent unreviewed major-version upgrades. `requirements.lock` is generated from that file and pins every resolved direct and transitive package version exactly.

## Milestone 1 lock provenance

- Target interpreter: CPython 3.12
- Target platform: clean Linux Mint 22.x, x86-64
- Resolver: `pip-compile` from `pip-tools`
- Source: `pyproject.toml` with the `dev` extra
- Command:

```bash
.venv/bin/pip-compile --extra dev --strip-extras --allow-unsafe \
  --output-file requirements.lock pyproject.toml
```

- Editable install: performed separately with `.venv/bin/python -m pip install --no-deps -e .`
- Machine-specific editable paths: prohibited from `requirements.lock`
- Build backend and installer tooling: included in the pinned snapshot; `--allow-unsafe` ensures pip and setuptools are recorded rather than silently omitted.

## Interpretation

The lock is a pinned resolver snapshot, not by itself proof of reproducibility on every platform. It intentionally omits distribution hashes because cross-platform hash enumeration was not adopted for this milestone; exact versions and a trusted package index are therefore both required. It becomes an independently verified clean-install lock only after installation in a newly created Python 3.12 environment, followed by `pip check`, package import, pytest, Ruff and the documented smoke checks. The final implementation report records which checks were actually run.

Regeneration is an intentional dependency change: update `pyproject.toml`, run the documented command, review every changed direct/transitive version, then repeat clean-install validation. Do not run an unconstrained upgrade as routine setup.
