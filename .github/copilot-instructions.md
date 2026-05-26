# GitHub Copilot — Repository Instructions

> This file is automatically loaded by GitHub Copilot (Chat, Code Review, and IDE integrations) to ground its responses in this repository's context. Keep edits concise and factual.

## Project identity

**Name**: GRS API Intake — Phase 1 of the Global Regulatory Sweep (GRS) v15.0 program.

**Single user**: A QA professional at a Korean pharmaceutical company that manufactures oral solid dosage forms (tablets). Korean domestic regulatory affairs are handled by a separate RA team, so this tool focuses exclusively on **global** GMP/CGMP regulatory changes (FDA, EMA, PIC/S, ICH, MHRA, Health Canada, TGA, PMDA, HSA, etc.).

**Not** a multi-tenant SaaS, **not** a clinical or pharmacovigilance tool, **not** for medical devices, cosmetics, food, biologics-only, or vaccines-only items. Filter rules in `collect_intake.py` reflect this scope and should not be relaxed casually.

## What this repo does

Runs a weekly GitHub Actions workflow that calls two public US-government APIs and stages the raw responses in a Notion database. A separate component (Claude Code Routine, not in this repo) reads the staged data the next morning to produce a curated weekly digest in another Notion database.

Architecturally:

```
[GitHub Actions: Sun 22:00 UTC]
        |
        | python collect_intake.py
        v
[Federal Register API]  +  [OpenFDA Drug Enforcement API]
        |                         |
        +------------+------------+
                     v
            [Notion "GRS API Intake" DB]
                     |
                     | (read by Claude Code Routine, Mon 07:30 KST)
                     v
            [Notion "Global Regulatory Sweep" DB — curated digest]
                     |
                     v
                  Human QA reviewer
```

## Repository layout

| File | Purpose |
|---|---|
| `collect_intake.py` | Python 3.12 collector. KST 7-day window, FR + OpenFDA pagination, deduplication, raw JSON preservation in Notion page body, QA-relevance heuristic. |
| `.github/workflows/grs-intake.yml` | Schedule (cron) + workflow_dispatch + auto-issue on failure. |
| `requirements.txt` | Only `requests`. Intentionally minimal — keep it that way. |
| `.gitignore`, `.env.example` | Python conventions. |
| `notion_intake_db_schema.md` | The 16 Notion DB properties — **must stay in sync with `PROP_*` constants in `collect_intake.py`**. |
| `GRS_Prompt_v15.0.md` | The Claude Code Routine prompt (the consumer of this collector). Not executed in this repo, but published here for traceability. |
| `setup.ps1`, `setup.sh`, `setup_guide.md` | One-shot bootstrap scripts for the maintainer. Not part of runtime. |
| `README.md` | User-facing setup guide. |
| `REVIEW.md` | Open questions and review focus for human/AI reviewers. |

## Domain conventions (do not violate)

- **Timezone**: All date arithmetic uses Asia/Seoul (KST, UTC+9). The Routine runs Monday 07:30 KST, and the collector runs 30 minutes earlier (Sunday 22:00 UTC = Monday 07:00 KST). UTC dates are never used as "today" — see `kst_run_date()`.
- **Window**: Exactly 7 calendar days in KST, inclusive of the run day. `[run_date - 7, run_date]`.
- **Evidence Level A** (gold standard): Only granted when the collector wrote raw API fields directly to a Notion row and those fields satisfy a specific completeness check (see `GRS_Prompt_v15.0.md` for the exact rule). Do not let `collect_intake.py` produce summarized text intended to be quoted as Evidence A.
- **L1 vs L2 URL**: Federal Register provides per-document URLs (L1). OpenFDA does not — it only offers an API; fall back to the FDA Recalls index page (L2: `https://www.fda.gov/safety/recalls-market-withdrawals-safety-alerts`). Never fabricate a per-recall URL.
- **Secret handling**: `NOTION_TOKEN` and `OPENFDA_API_KEY` live in GitHub Secrets only. The collector reads via `os.environ`. They must never appear in code, logs, commit messages, or page content. The `_mask_api_key` helper redacts them in any log line we generate.
- **Graceful degradation**: If one API fails, the other still runs and the workflow exits 0. Only both-failing returns exit 1. The downstream Routine handles a 0-row intake by falling back to its WebSearch-only mode (v14.5 behavior).
- **Notion property names** are the single source of truth — they must match `PROP_*` constants character-for-character including spaces and parentheses (e.g. `"Run Date (KST)"`).

## Intentional design choices (please don't "fix")

- **Single dependency** (`requests`) — adding more deps is a tax on every CI run and every reviewer; new deps need explicit justification.
- **No retry library** — handcrafted exponential backoff (2 retries, `2**attempt` seconds) is enough for these two APIs. Don't pull in `tenacity` or `urllib3.Retry` for this.
- **No Notion SDK** — the REST API is used directly. The SDK adds opaque error wrapping and isn't worth the dependency for ~3 endpoints.
- **No ORM / dataclass DB layer** — `IntakeItem` is a plain dataclass and `build_notion_properties()` constructs the dict in one place. Don't introduce schema validation libraries.
- **QA Relevance heuristic stays simple** — it's a hint, not a filter. The Routine's LLM does the real classification using the raw payload in the page body. The heuristic is intentionally conservative (defaults to `Pending`).
- **`per_page=100` for FR, `limit=100` for OpenFDA** — chosen for safety vs. completeness. A typical week has 10-20 FR items and 0-10 recalls, so this fits in one page. Pagination is implemented in case the upper bound is hit, but rarely runs.

## Known limitations (documented, not bugs)

1. The QA Relevance heuristic uses ICH/CGMP terminology (CGMP, OOS, Annex 1, …). Real OpenFDA recall texts use plainer English (`Failed Dissolution Specifications`, `Particulate matter`), so they often land in `Pending`. **This is fine** — final relevance is decided by the Routine. Phase 3 may expand the heuristic vocabulary.
2. OpenFDA does not expose per-recall friendly URLs. Until they do, every Recall row's `Official URL` is the L2 index. Reviewers may suggest constructing FDA `iRES` URLs from `recall_number`, but those are not documented and routinely break.
3. Federal Register may return a Notice that is announcing a Draft Guidance whose actual content sits at a different URL (e.g., regulations.gov docket). We capture the FR notice only; following links to the regulations.gov docket is out of scope for Phase 1.
4. Cloud-managed Claude Code Routine has egress restrictions, so the Routine cannot call FR/OpenFDA directly. **This collector existing as a separate GitHub Actions job is the whole reason for v15.0** — that context matters when reviewing why we don't just call APIs inside the Routine.

## Conventions for changes

- Korean comments and Korean log strings are OK in `setup_guide.md` and `README.md`, but **not** in `.ps1` files (Windows PowerShell 5.1 falls back to CP949 if BOM is missing — we keep `.ps1` files ASCII-only with UTF-8 BOM as defensive armor; see git history for the bug that motivated this).
- Markdown files use UTF-8 without BOM.
- Python: type hints required on function signatures; docstrings encouraged but not enforced; no `print()` — use the `log()` helper which timestamps and prefixes a level.
- Commit messages: imperative mood, English preferred but Korean acceptable; no token-like strings (`secret_…`, `ntn_…`, `sk-…`) in any commit content (GitHub Push Protection enforces this; we triggered it once during initial setup).

## Out of scope for this repo

- EMA, PIC/S, ICH, TGA, MHRA, Health Canada, PMDA, HSA API integrations — these are Phase 2 candidates and not all have usable public APIs.
- Web scraping of news sites — that's the Routine's WebFetch responsibility, deliberately kept separate to maintain trust boundaries (Evidence Level A vs. B/C).
- Sending notifications (email, Slack) — the human reviewer reads the Notion page on Monday morning. No notification layer is desired or planned.
- Web UI / dashboard — Notion is the UI.

## Useful starting points for review

- `collect_intake.py::main()` — top-down entry point, ~80 lines.
- `collect_intake.py::collect_federal_register()` and `::collect_openfda_recalls()` — the two API integrations.
- `collect_intake.py::build_notion_properties()` / `::build_notion_children()` — Notion adapter.
- `.github/workflows/grs-intake.yml` — schedule, secrets surface, failure handler.
- `notion_intake_db_schema.md` — the DB contract (one side of the property-name agreement).
