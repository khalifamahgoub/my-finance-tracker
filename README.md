# Personal Finance Tracker

A local, one-command pipeline that turns monthly bank/credit-card PDF statements into a
dashboard and a plan-vs-actual variance report. Boring, maintainable Python; SQLite is
the source of truth; no cloud required.

> The spec lives in [`PRD.md`](PRD.md) (sanitized). The `config/*.yaml` you actually run
> with are gitignored; only sanitized `config/*.example.yaml` templates are committed.

## Build status

[![tests](https://github.com/khalifamahgoub/my-finance-tracker/actions/workflows/tests.yml/badge.svg)](https://github.com/khalifamahgoub/my-finance-tracker/actions/workflows/tests.yml)

Every push and pull request runs the full `pytest` suite on Python 3.13 via GitHub
Actions ([`.github/workflows/tests.yml`](.github/workflows/tests.yml)) — 48 unit tests
covering the financial-month math, dedup/normalisation, internal-transfer netting, RAG
variance, cash-flow forecasting, month-to-date pacing, and subscription-change detection.
The two ila-account reconciliation tests run locally against the real statements and skip
on CI (where `Docs/` is gitignored), so the build stays green without exposing any data.

## Quick start (zero install)

Everything the pipeline needs is in the standard library plus `pymupdf`, `pandas`,
`openpyxl`, `PyYAML`, `jinja2`. Then:

```
# 1. create your real config from the templates
cp config/accounts.example.yaml config/accounts.yaml
cp config/iban_map.example.yaml config/iban_map.yaml   # or let `finance review` build it
# 2. drop statement PDFs (+ the plan .xlsx) into inbox/
# 3. run it
python -m finance run          # or: finance.cmd run   (Windows shim)
```

`finance run` ingests everything in `inbox/`, categorises, nets internal transfers, and
writes `output/dashboard.html` + a Markdown summary.

## Commands

| Command | Does |
|---|---|
| `finance run` | Process `inbox/`, regenerate the dashboard + Markdown |
| `finance run --sync` | …and push a projection to Notion (needs `NOTION_TOKEN`) |
| `finance run --narrate` | …and print an AI narrative (needs `ANTHROPIC_API_KEY`) |
| `finance review` | Interactively name unknown IBANs/merchants (learns permanently) |
| `finance period "Feb 2026"` | Regenerate any past month |
| `finance sync-notion [--dry-run]` | Push/preview the one-way Notion projection |
| `python -m pytest` | Run the test suite |

## How it works

- **Financial month = 23rd → 22nd**, named by the month containing the 22nd.
- **Native-text parsers** for three account types; each reconciles to its statement's
  own printed totals to the fils. OCR is a dormant fallback (not needed today).
- **Idempotent ingest**: file-hash skip + a per-row dedup key (occurrence-aware, so
  genuine same-day/same-merchant duplicates survive while re-downloads collapse).
- **Categorisation + netting**: transfers between your own accounts never count as
  spend; credit-card purchases are the real spend, counted once.
- **Plan vs actual** from an annual cash-flow sheet, with RAG status, standing flags,
  and school/emergency sinking funds.
- **Notion sync** is a one-way, idempotent projection — SQLite never loses authority.

## Layout

```
finance/          the package (cli, parsers/, categorise, transfers, plan, variance,
                  report_html/md, sinking, notion_sync, …)
config/           *.example.yaml (committed) + your real *.yaml (gitignored)
inbox/            drop statements here      archive/YYYY-MM/  processed files
output/           dashboard.html + summaries
tests/            pytest suite
```

## Privacy

Gitignored (never committed): `Docs/` (raw statements), `finance.db`, `output/`,
`archive/`, the raw handover note, and the real
`config/{accounts,categories,iban_map,notion,learned}.yaml`. Only sanitized templates,
a sanitized `PRD.md`, and code are tracked, so the repo is safe to push.
