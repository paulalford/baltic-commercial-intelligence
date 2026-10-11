# Baltic Sales Intelligence

Baltic Sales Intelligence is a portfolio-grade commercial analytics and decision-support case study for **Baltic Trade Supplies UAB**, an entirely fictional B2B distributor serving Lithuania, Latvia and Estonia.

All future customers, employees, suppliers, transactions and commercial results are synthetic. This repository does not represent a real client engagement, deployed system or measured business outcome.

## Current status

**Milestone 2 — Synthetic Reference Data.** The project generates and validates the
SPEC-03/SPEC-05 reference population: geography, controlled taxonomies, customers and
SCD Type 2 history, salespeople and account ownership, products and effective-dated
supplier relationships, and the extended date dimension. Commercial orders, order lines,
returns and sales targets remain intentionally unimplemented for Milestone 3.

## Technology foundation

- Python 3.12
- pandas and PyArrow
- DuckDB
- Pandera
- SciPy, statsmodels and scikit-learn
- Plotly and Streamlit
- Pydantic and PyYAML
- pytest, pytest-cov and Ruff

`pyproject.toml` is the authoritative source for direct dependencies and supported version ranges. `requirements.lock` is the exact resolved transitive snapshot used for this milestone. See [Dependency strategy](docs/development/DEPENDENCIES.md) for provenance and limitations.

## Repository structure

```text
app/                         Minimal Streamlit entry point and future UI modules
config/                      Four versioned, validated YAML configuration files
data/                        Ignored generated-data layers; tracked markers only
docs/                        Approved specifications, decisions and audits
scripts/                     Thin wrappers for six future pipeline stages
sql/                         Empty staging/dimension/fact/mart/analysis/test layers
src/baltic_sales_intelligence/
  generation/                Deterministic reference generators and validators
  pipeline/                  CLI parser and stage dispatch
  quality/ ... insights/     Reserved later-stage domain packages
  config.py                  Strict typed configuration loader and hash
  logging.py                 Structured JSON logging
tests/
  unit/                      Imports, config, logging and CLI behaviour
  integration/               Git exclusions and Streamlit placeholder
  generation/                Reference generation and integrity tests
  analytics/                 Reserved for later milestone tests
```

## Linux Mint / Python 3.12 setup

The validated baseline is Linux Mint with CPython 3.12. Create a repository-local environment:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
```

The editable package is deliberately installed separately and is not embedded as a machine-specific path in the lock file.

To regenerate the lock after an intentional `pyproject.toml` dependency change:

```bash
.venv/bin/python -m pip install "pip-tools>=7.4,<8"
.venv/bin/pip-compile --extra dev --strip-extras --allow-unsafe \
  --output-file requirements.lock pyproject.toml
```

Review the diff and validate a fresh Python 3.12 environment before treating a new snapshot as verified.

## Configuration

The loader expects all four files:

- `config/application.yaml` — identity, markets, reporting window and logging.
- `config/generation.yaml` — specification-defined scale, catalogue and seed identifiers.
- `config/data_quality.yaml` — defect catalogue and silver publication gates.
- `config/analytics.yaml` — approved customer, product, forecast and opportunity settings.

Load and hash the validated bundle:

```python
from baltic_sales_intelligence import load_configuration

configuration = load_configuration("config")
print(configuration.deterministic_hash())
```

Unknown keys, duplicate YAML keys, missing files and invalid weights or taxonomies fail
explicitly. Population counts and the master seed may be deliberately changed while the
approved market and taxonomy contract remains fixed.

## Pipeline CLI

```bash
python -m baltic_sales_intelligence.pipeline --help
python -m baltic_sales_intelligence.pipeline --config config/ --stage generate
```

The generation command writes canonical UTF-8 CSV and typed Parquet files to
`data/truth/`, plus `generation_manifest.json` and `distribution_summary.csv`. Use
`--dry-run` to generate and validate without publishing, `--output PATH` for an alternate
truth directory, and `--force` when replacing a build made from a different config hash.

Valid stage names are `generate`, `inject-defects`, `validate`, `warehouse`, `analytics` and `insights`.

| Exit code | Meaning |
|---:|---|
| 0 | Help or a genuinely successful implemented operation |
| 2 | Command-line usage or invalid stage |
| 3 | Missing or invalid configuration |
| 4 | Requested feature/stage is explicitly not implemented |
| 5 | Reserved for a future implemented-stage failure |

The other five stages still return exit code 4. Thin wrappers under `scripts/` use the same
dispatcher and failure contract.

## Logging

Pipeline logs are one JSON object per line and include `stage`, `dataset`, `batch_id`, `action`, `count` and `error_category`. Logging levels come from validated configuration. APIs intentionally accept event metadata rather than complete business-row payloads.

## Tests and linting

```bash
.venv/bin/python -m pip check
.venv/bin/python -m pytest -v
.venv/bin/ruff check .
.venv/bin/python -c "import baltic_sales_intelligence"
```

The Streamlit placeholder is exercised with Streamlit's application test API. To view it manually:

```bash
.venv/bin/streamlit run app/Home.py
```

## Planned pipeline

The approved future flow is:

```text
versioned config → synthetic truth → defect-injected raw → validation/quarantine
→ silver Parquet → DuckDB warehouse → SQL marts → analytics/insights
→ read-only Streamlit presentation
```

## Explicit limitations

- Reference truth is synthetic and generated locally; generated files remain Git-ignored.
- No orders, order lines, returns or sales targets are generated in this milestone.
- No financial formula, VAT posting, return recognition or target calculation is implemented.
- No defect injection, silver validation, DuckDB schema, SQL mart or analytical model exists.
- No forecasting, anomaly, opportunity or management finding is produced.
- Milestone 2 tests cover generator contracts, not later financial or analytical gates.
- The lock snapshot records one resolved environment; independent clean-install verification must be reported separately.

The implementation intentionally stops at the Milestone 2 reference-data boundary.
