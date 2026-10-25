# Nordic BESS revenue benchmark — project rules

## Goal

Answer one question: **"Where does a 1 MW / 1 MWh battery in Finland earn its money, and how has that
changed over the last 12 months?"** Public portfolio piece for a Market Analyst application at Modo
Energy. Reviewers read the code: keep it lean, correct, tested, easy to follow. Every step has a short
comment that explains *why* it exists.

Work in phases (see the original brief). At the end of each phase: stop, show the work and test
results, wait for "go". One small Conventional Commit per phase (`feat:`, `test:`, `docs:`, `ci:`,
`chore:`), pushed to GitHub after "go".

## Hard rules

1. **Never invent API details.** Read the official docs first; confirm endpoint, auth header, query
   parameters, pagination, rate limits and timestamp format. Report findings with the doc URL.
2. **Never commit secrets or raw data.** API keys live only in `.env`. `data/raw/`, `data/clean/` and
   `.env` are git-ignored.
3. **Keep it lean.** Allowed deps: `pandas`, `pyarrow`, `requests`, `python-dotenv`, `matplotlib`,
   `entsoe-py` (phase 8 only). Dev: `pytest`, `ruff`, `mypy`, `pandas-stubs`, `types-requests`,
   `pre-commit`. Ask before adding anything else.
4. **Pure logic, separate from I/O.** Validation and revenue functions take DataFrames and return
   DataFrames. They never call APIs or read files.
5. **All timestamps are timezone-aware UTC** inside the pipeline. Convert at the edges only.
6. **No silent data changes.** Never drop, fill or clip without logging how many rows and why.
   Negative prices are valid data; never drop them.
7. **Tests never call live APIs.** Use small fixture files in `tests/fixtures/`.
8. **Windows user.** Commands must work in PowerShell or Git Bash. Use `pathlib` for all paths.
9. **If something is unclear or a check fails, stop and ask.** Do not guess.

## Commands

Python 3.12 is managed by `uv` (no system Python on this machine).

```bash
uv venv --python 3.12 .venv            # once
uv pip install -e ".[dev]"             # add ,entsoe for phase 8
.venv/Scripts/pre-commit install       # once
```

Run with the venv active (`.venv\Scripts\activate` in PowerShell, `source .venv/Scripts/activate` in
Git Bash):

```bash
pytest                                 # tests
ruff check .                           # lint
ruff format --check .                  # format check
mypy src                               # type check
python -m nordic_bess.pipeline [--config params.toml] [--start YYYY-MM-DD --end YYYY-MM-DD] [--refresh]
```

All user inputs (periods, battery size, efficiency, availability) live in `params.toml`, each with
a comment saying why it has that value. Market rules (Fingrid Table 10) are constants in
`config.py` with their source. Never hard-code an input in code; never make a market rule an input.

## Definition of done

- `pytest`, `ruff check`, `ruff format --check` and `mypy src` pass locally and in CI.
- One command rebuilds every output from the raw data.
- Every function has type hints and a one-line docstring; every non-obvious step has a "why" comment.
- No secrets, raw or clean data in Git history (`git log --all -- data/raw .env` returns nothing).
- README numbers match `data/output/summary.json` exactly.
