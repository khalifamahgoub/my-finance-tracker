# Personal Finance Tracker

[![tests](https://github.com/khalifamahgoub/my-finance-tracker/actions/workflows/tests.yml/badge.svg)](https://github.com/khalifamahgoub/my-finance-tracker/actions/workflows/tests.yml)

A local, one-command pipeline that turns monthly bank/credit-card PDF statements into a
dashboard and a plan-vs-actual variance report. Boring, maintainable Python; SQLite is
the source of truth; no cloud required.

> The detailed spec lives in `PRD.md`, which is **gitignored** because it contains
> personal financial detail. The `config/*.yaml` you actually run with are gitignored
> too — only sanitized `config/*.example.yaml` templates are committed.

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
`archive/`, `PRD.md`, and the real `config/{accounts,iban_map,notion,learned}.yaml`.
Only sanitized templates and code are tracked, so the repo is safe to push.
