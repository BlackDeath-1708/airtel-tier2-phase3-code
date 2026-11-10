# Airtel Phase 3 traceroute scan: code

This repository holds the scan code and its GitHub Actions workflow. It does not hold any scan data.
The progress, the results and the target list live in a private repository, which the scan reads and writes.

## What runs where

- **GitHub Actions** (`.github/workflows/phase3-scan.yml`) runs the scan on a schedule every 6 hours, and on demand.
- **A PC** can run the same scan with `scripts/phase3-run-local.sh`. Only one runner works at a time: a lock
  in the state repository stops a second runner from starting while the first is working.
- **The live page** (`live/app.py`) reads the state repository and shows the progress. It can be deployed on
  Streamlit Community Cloud.

## Secrets

| Where | Name | What it is |
| --- | --- | --- |
| This repository (Actions) | `GLOBALPING_TOKENS` | Comma-separated Globalping API tokens |
| This repository (Actions) | `STATE_TOKEN` | Fine-grained token that can read and write the private state repository only |
| Streamlit | `GITHUB_REPO`, `GITHUB_READ_TOKEN`, `APP_PASSWORD` | Read-only access to the state repository, and the page password |

## Layout

- `phase3/`: the scan (planning, probes, runs, sync with the state repository, the summary for the live page)
- `live/`: the live status page
- `gui/phase3_data.py`: reads the scan folder for the local GUI
- `tests/`: unit tests (`python -m pytest`)
