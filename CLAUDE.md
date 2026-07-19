# CLAUDE.md — Personal Finance Tracker conventions

Read [PRD.md](PRD.md) for the full spec. This file captures the load-bearing conventions
so future sessions stay consistent. **Follow these exactly.**

## What this is
A local, one-command pipeline: drop PDF statements + the plan `.xlsx` into `inbox/`, run
`finance run`, get `output/dashboard.html` + a Markdown summary. Boring, maintainable
Python over cleverness. Glance-first: one entry point, flat structure.

## Run it (zero install)
Everything is already in the system Python (3.13): pymupdf, pandas, openpyxl, PyYAML,
Jinja2, stdlib sqlite3. Just:
- `finance.cmd run` (or `python -m finance run`) — the one command.
- `python -m finance init` — bootstrap/seed the DB. `python -m pytest` — tests.
- Optional isolation: `uv sync --native-tls` (uv + truststore handle a TLS-inspecting proxy).

## Hard domain rules (do not violate)
- **Financial month = 23rd → 22nd**, named by the month containing the 22nd ("Feb 2026" =
  23 Jan–22 Feb). All bucketing goes through `finance/periods.py`. Salary lands ~25th → next period.
- **Dedup key** = `sha1(source_account | date | amount.3f | norm_desc)`, scoped per source
  (`finance/normalise.py`). Re-runs are idempotent (`ON CONFLICT(dedup_key) DO NOTHING`).
- **Internal transfers net out.** `own_account_ibans` (listed in `config/accounts.yaml`) +
  CC "Payment Received" + ila "Credit Card Payment" are `is_internal=1`; excluded from spend/income.
  CC line-item purchases are the real spend, counted once.
- **Term fees are a sinking fund.** ≥3800 BHD to the school IBAN = term fee (`is_sinking`);
  smaller = ECA/monthly.
- **Money in fils** (REAL, 3 dp). Reconcile at 0.001; category acceptance gate BHD 1.
- **BHD only.** AED/SAR accounts are out of scope for v1.

## Architecture
- **SQLite (`finance.db`) is the system of record + compute engine.** Notion is an optional
  projection (`finance sync-notion`, Phase 4). It does not feed back, with ONE scoped
  exception: `sync-notion --pull` reads the Review Queue's Category/Payee tags back and learns
  them (IBAN → `iban_map` confirmed; merchant → `learned.yaml`) via the same `review.py`
  helpers, then re-categorises. Everything else in Notion stays read-only-derived.
- **Config is data, not code.** `config/*.yaml` (iban_map, categories, rules, accounts) is
  hand-editable; the DB seeds from it. Review-confirmed IBANs → `iban_map` (source='confirmed',
  never clobbered by re-seed); confirmed keywords → `rules.yaml`.
- **Parsers are native-text first** (`finance/parsers/`), dispatched by an ordered registry +
  `detect()`. The Khaleeji OCR parser is a dormant fallback: it lazily imports `pytesseract`
  and self-disables unless `ocr.tesseract_cmd` is set and the binary exists. Today's corpus is
  100% native text — OCR does not fire.
- **Reconcile extraction at the statement level** (each statement prints its own totals), then
  aggregate to periods for reporting. Statement cycles (CC 20th–19th, Khaleeji calendar-month)
  do NOT align with the financial month, so the newest period is always partially incomplete.

## Sources (all native text)
- `khaleeji` — `AccountFullstatement_*.pdf` (filename = download date, NOT statement month;
  derive coverage from `Statement From…To…`).
- `ila_cc` — `Statement-YYYYMMDD.pdf` in the ila CC folder. Two cardholders: sections
  `Transactions on Card ending-XXXX` (primary) / `Supplementary Card ending-YYYY` (supplementary),
  mapped by last-4 in `config/accounts.yaml`. FX purchases = merchant line + FOREIGN EXCHANGE
  MARKUP + VAT (all real cost); `CR` = refund.
- `ila_account` — `Statement-YYYYMMDD.pdf` in the ila Debit folder (an own current account).
- Scope statements by **folder**, not the `Statement*` glob (it also matches debit + SAR/AED).

## Layout
`finance/` package (cli, config, db, periods, normalise, ingest, parsers/, categorise,
transfers, plan, variance, sinking, report_html, report_md, notion_sync, webapp). `config/` YAML seeds.
`webapp` is a stdlib-only localhost app (`finance web`) that serves the live dashboard and a
tag form; it reuses `review.py`'s learning helpers so tagging is identical across CLI/Notion/web.
`inbox/`→`archive/YYYY-MM/`. `output/` dashboard + summaries. `tests/`. Raw statements live in
`Docs/` (gitignored); the initial backfill copies them into `inbox/`.
