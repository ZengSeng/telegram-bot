---
kind: dependency_management
name: Python Dependencies via requirements.txt with Virtual Environment
category: dependency_management
scope:
    - '**'
source_files:
    - requirements.txt
---

This repository uses the standard Python dependency management approach with a single `requirements.txt` file at the repository root to declare third-party packages. The project has only two external dependencies:

- `python-telegram-bot[job-queue]==21.6` — pinned to an exact version, includes the optional `job-queue` extra for scheduled tasks
- `yfinance` — used for stock market data retrieval (no version pinning)

There is no `Pipfile`, `pyproject.toml`, `setup.py`, or `poetry.lock` — the project relies on the minimal `requirements.txt` convention. A local virtual environment exists under `venv/` but appears empty in this snapshot, suggesting it was created but not fully populated or committed.

No vendoring strategy is used (no `vendor/` directory), and there are no private registry configurations, environment-specific requirement files (`requirements-dev.txt`, etc.), or lockfiles beyond what pip generates locally. Dependency updates are manual — developers edit `requirements.txt` directly and reinstall via `pip install -r requirements.txt`.

The Python source files import from both standard library modules (`asyncio`, `datetime`, `json`, `logging`, `subprocess`, `pathlib`) and the declared third-party packages, with no additional hidden dependencies detected in the imports.