# PRD: Personal Finance Tracker (Bahrain, multi-account)

A local, one-command pipeline that turns monthly PDF bank/card statements plus a plan
spreadsheet into an HTML dashboard and a plan-vs-actual variance report. Build in small,
verifiable increments: after each phase, show the output on real data before moving on.

> This is a **sanitized** spec. The author's real directory, amounts, and family/account
> specifics live only in the gitignored config and the raw handover note; every value
> below is a generic placeholder.

## 1. Problem

Finances run on a monthly cycle: download PDF statements from a few sources, extract
transactions, categorise, reconcile against an Excel plan, and produce a report — today
done manually each cycle. The goal is a repeatable local pipeline: drop files in a folder,
run one command, get a dashboard plus a variance report. Zero re-explaining each month.

## 2. User

Single user. Prefer boring, maintainable Python over clever architecture. Glance-first is
a hard requirement: one entry point, flat structure, no nested menus. The answer to "how
am I doing this month?" must be one command away.

## 3. Data sources

### 3.1 Bank current account (BHD) — PDF
- Filename pattern: `AccountFullstatement_<acct>_DDMMYYYY[timestamp].pdf`.
- Columns: date, description, debit, credit, balance. Descriptions embed beneficiary IBANs
  for transfers. (If a statement is a genuine image scan, OCR via PyMuPDF pixmap +
  `pytesseract --psm 6`; today's corpus is native text, so OCR is a dormant fallback.)

### 3.2 Credit card (BHD) — native-text PDF
- Filename pattern: `Statement-YYYYMMDD.pdf`.
- Text extracts cleanly with PyMuPDF. Two cardholders on one statement (primary +
  supplementary); tag cardholder per transaction where the statement distinguishes them.

### 3.3 Financial plan — Excel
- One workbook (annual or per-period), e.g. `Financial_Plan_<Mon><Year>.xlsx`. Read with
  `pandas.read_excel(sheet_name=None)`. It is the plan-vs-actual benchmark; treat plan
  categories as the canonical list and map transaction categories to them.

## 4. Core domain rules (do not violate)

1. **Financial month runs 23rd to 22nd**, not calendar month. All grouping, budgets, and
   reports use this period, named by the month containing the 22nd ("Mar 2026" = 23 Feb–22 Mar).
2. **Dedup key**: source + date + amount + normalised description (occurrence-aware within a
   statement). Same combination = one transaction. Needed because statements overlap and
   re-downloads happen.
3. **Transfers between your own accounts are not spending.** Funding an own account and the
   credit-card payment leg must net out; the card's line-item purchases are the real spend,
   counted once. Own-account IBANs are listed in `config/accounts.yaml`.
4. **Term fees are lump sums, not monthly spend.** A large payment (≥ a configurable
   threshold) to the term-fee IBAN = a term fee, tracked against a sinking fund; smaller
   amounts to the same IBAN are ordinary spend.
5. **A recurring remittance** may look irregular in bank data alone (digital transfer + cash
   together total the planned amount). Flag shortfalls as "likely cash top-up", not variance.
6. **One-off transfers** are tagged separately from a recurring remittance; a spike to a
   shared recipient IBAN is not automatically a budget breach.
7. Currencies: BHD primary. Other-currency accounts are out of scope for v1.

## 5. IBAN directory (seed the mapping table)

Seed `iban_map` from a hand-editable directory that maps each beneficiary IBAN to a payee
and category, plus `is_internal` for own accounts and a `REVIEW` flag for unknowns. Your
real directory lives in the gitignored `config/iban_map.yaml`; the committed
`config/iban_map.example.yaml` shows the format. Illustrative shape:

| IBAN (example) | Maps to | Category | Notes |
|---|---|---|---|
| BH00EXAM…01 | Landlord | Housing | |
| BH00EXAM…02 | Own account | Internal Transfer | `is_internal: 1` |
| BH00EXAM…03 | Term-fee recipient | Education | ≥ threshold = term fee (sinking) |
| BH00EXAM…04 | Recurring remittance | Recipient B | shared IBAN, amount-split |
| BH00EXAM…05 | UNKNOWN | Uncategorised | `flag: REVIEW` — flag every occurrence |

## 6. Categorisation

- Rules-based first: keyword + IBAN matching from a hand-editable `rules.yaml`. Examples:
  TALABAT/JAHEZ → Food Delivery; NETFLIX/GOOGLE ONE/CLAUDE.AI/PADDLE/APPLE.COM → Subscriptions;
  fuel stations → Transport.
- Unmatched rows land in `Uncategorised` and appear in a review list. Confirmed mappings are
  learned (IBANs → `iban_map`; keywords → `rules.yaml`) so accuracy compounds monthly.
- Standing flags: **Food Delivery** (known leak; show plan vs actual vs 3-month average),
  **Telecom** (trend), **Subscriptions** (enumerate individually so dead ones surface).

## 7. Storage

- SQLite, single file `finance.db`. Tables: `transactions`, `categories`, `iban_map`,
  `plan_lines`, `periods`, `sinking_funds` (+ `source_files`). Plain SQL, no ORM.
- Raw PDFs sit in `inbox/` until processed, then move to `archive/YYYY-MM/`. Idempotent:
  re-running on an already-processed file changes nothing (dedup key enforces this).

## 8. Outputs

1. **HTML dashboard** (single self-contained file — the one entry point). Sections in order:
   headline (income/spend/net/savings rate); plan vs actual with RAG status (🟢 within plan,
   🟡 up to 15% over, 🔴 more than 15% over); category breakdown; standing flags; sinking-fund
   tracker (term fund by next due month, emergency fund vs target); investable surplus (net
   after obligations + sinking = suggested investment transfer); uncategorised review list.
2. **Markdown monthly summary** for pasting into Notion (blunt tone, findings first).

## 9. CLI

`finance run` (process inbox + regenerate dashboard). Plus `finance review` (resolve
uncategorised), `finance period <name>` (regenerate an old month), and optional
`finance sync-notion` / `finance narrate`.

## 10. Phases

- **Phase 1**: parsers (native text first), SQLite schema, dedup. Prove extraction accuracy
  against one known month before anything else.
- **Phase 2**: categorisation + `rules.yaml` + IBAN mapping, plan ingestion, variance.
- **Phase 3**: dashboard + Markdown report + sinking/emergency tracking.
- **Phase 4**: one-way Notion projection; forward cash-flow forecast, pacing, AI narrative.

## 11. Non-goals

No cloud, no auth, no multi-user, no mobile app, no bank API integration, no ML
categorisation. Local Python + SQLite + static HTML.

## 12. Definition of done (v1)

Given the last three months of statements plus the plan Excel, `finance run` produces a
dashboard whose totals match a manual reconciliation within BHD 1 per category, with fewer
than 5% of transactions uncategorised.
