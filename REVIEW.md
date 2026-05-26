# Review Request — GRS API Intake (v15.0 Phase 1)

This document orients a reviewer (human or AI) toward the right questions for this repository. It pairs with `.github/copilot-instructions.md`, which holds the durable project context. **Reviewer should read both before commenting.**

## Status (as of first production run)

- ✅ First production run on **2026-05-26 KST** completed without errors.
- ✅ Federal Register: 12 / 12 inserted · OpenFDA Recall: 3 / 3 inserted.
- ✅ Validated against the two items that the v14.5 (WebSearch-only) baseline had missed: `Recall D-0547-2026 Ascend Laboratories — Failed Dissolution Specifications` (Metoprolol Succinate ER Tablets — directly oral solid dosage relevant) and `FR 2026-10277 Product-Specific Guidances for Industry`. Both are now captured in `GRS API Intake`.
- ⏳ Awaiting the first Routine v15.0 run (next Monday 07:30 KST) to validate the end-to-end loop.

## What I want the review to focus on

In priority order. Items lower on the list are nice-to-have.

### 1. Security & secret handling
- Confirm `NOTION_TOKEN` and `OPENFDA_API_KEY` never leak via:
  - process arguments (`gh secret set` is invoked with `--body $content` — content is passed via parameter binding rather than positional arg; is this safe enough?),
  - log lines (`_mask_api_key()` handles the API key in URL form — any other vectors?),
  - error messages bubbled up from `requests` or `gh`,
  - the temp file pattern in `Set-RepoSecret` (PowerShell) — should we use `[System.IO.Path]::GetRandomFileName()` in a more restricted ACL'd directory?
- Confirm the workflow's `permissions:` block (`contents: read`, `issues: write`) is the minimum needed.
- One historical mistake: an earlier README revision embedded a real `ntn_…` token in markdown text. GitHub Push Protection caught it pre-push. Anything similar lurking?

### 2. Correctness of the time window
- `kst_run_date()` and `date_window()` — do they handle DST edge cases correctly given that KST has no DST but the GitHub Actions runner runs in UTC?
- A workflow scheduled for `0 22 * * 0` runs at Sunday 22:00 UTC. We treat the run date as `(now_utc + 9h).date()` which is Monday in KST. Is there any edge case where `now_utc` could land at 22:59 UTC on Saturday (i.e., 07:59 KST Sunday) due to scheduler skew? GitHub Actions cron is "best effort" — should we accept that and document, or anchor on the workflow file's hardcoded day?
- The 7-day window is **inclusive at both ends** in our SQL-style query (`gte` and `lte`). Federal Register treats `publication_date[lte]` as inclusive; OpenFDA `report_date:[A+TO+B]` is also inclusive. Double-check?

### 3. API correctness and resilience
- Federal Register: we follow `next_page_url` until `null` or until 10 pages (safety). For the realistic weekly volume (~10-20 items), one page is enough. Is the 10-page guard reasonable?
- OpenFDA: `404` from the API when the result set is empty is treated as "0 results, normal." Confirm this is the actual API behavior (it is, per `open.fda.gov/apis/responses/`). Is there a better way to detect "zero results" without parsing the error message?
- Retry: `http_get_json` retries twice with `2^attempt` seconds. Should this be longer? Should it distinguish transient vs. permanent failures (4xx vs. 5xx)?

### 4. Notion adapter correctness
- `build_notion_properties()` — every property is built unconditionally except optional ones (Firm, Distribution, Comments Close), which are skipped via `if item.field:`. Is there a subtle bug where an empty string for an optional field would be omitted but a `None` would crash?
- `build_notion_children()` chunks the raw JSON into 1900-char blocks because Notion's `rich_text.content` is capped at 2000 chars. Are there edge cases where a chunk boundary lands inside a JSON escape sequence and breaks the visual rendering (it would still parse — Notion doesn't try to parse the JSON, it just displays). Should we prefer line-boundary chunking?
- `notion_query_existing_doc_ids()` paginates with `start_cursor`. The safety cap is 20 pages × 100 = 2000 rows. For a single Run Date this is far more than realistic. OK?

### 5. Idempotency & duplication
- The dedup key is `(Run Date (KST), Document ID)`. If a user re-runs the workflow on the same Monday (intentionally or by accident), the second run finds existing IDs in Notion and skips them. Confirm this works for both successful and partial first runs.
- What happens if the workflow runs twice within seconds (e.g., user clicks Run workflow twice)? Notion may not have visibility into in-flight inserts; could we get a race? (Probably not — workflow_dispatch is single-instance and concurrent runs of `concurrency: grs-intake` are blocked at the GitHub level. Still worth confirming.)

### 6. PowerShell script robustness
- `setup.ps1` is intentionally ASCII-only with UTF-8 BOM after a real bug where Windows PowerShell 5.1 mis-decoded a BOM-less UTF-8 file as CP949. Confirm the current file is still ASCII-only (line-level grep for `[^\x00-\x7F]` should match only the BOM at offset 0).
- `Invoke-Native` helper resets `$ErrorActionPreference` around native calls because EAP=Stop turns native stderr into terminating errors. Are there code paths where we forget to use `Invoke-Native` and a native call could still throw?
- The script is also intended to be idempotent: re-running on an existing repo or with existing secrets should succeed. Does it?

### 7. CI/CD configuration
- `.github/workflows/grs-intake.yml`:
  - `concurrency: grs-intake` blocks parallel runs. Good.
  - `timeout-minutes: 10` — plenty for a 2-minute job. Should we lower to 5?
  - The `failure()` step opens a GitHub issue on schedule-triggered failure. Is the issue title format good for grep / triage?
  - The cron `0 22 * * 0` — confirm it parses as "Sunday 22:00 UTC" (some cron implementations treat the day-of-week 0 differently).

## What I am NOT looking for in this review

These are intentional and well-documented. Suggesting "fixes" for them adds noise:

- Adding more Python dependencies (`tenacity`, `notion-client`, `pydantic`, etc.).
- Adding a database/ORM layer between the collector and Notion.
- Expanding the QA Relevance heuristic vocabulary — Phase 3 work, not Phase 1.
- Building a web UI / dashboard. Notion is the UI.
- Notifications (email, Slack, etc.). Out of scope.
- Multi-tenancy or generalization beyond the single-user/Korean-oral-solid-dose scope.
- Re-architecting around AWS Lambda or a different cloud — GitHub Actions is the decision.

## Specific questions I'd like answered

1. Is the `--break-system-packages` pip flag (currently NOT used because we have `requirements.txt` and `setup-python` with `cache: pip`) needed anywhere? My read is no, but I'd like a second opinion.
2. Should the workflow set `pythonioencoding: utf-8` in the env block to make Notion's non-ASCII outputs render correctly in the GitHub Actions log? (Currently runs on Ubuntu so probably moot, but defensive.)
3. The `New-TemporaryFile` in `setup.ps1` is created in `$env:TEMP` which on Windows is `C:\Users\<user>\AppData\Local\Temp`. This directory is user-private but not encrypted at rest. The temp file holds the secret only for milliseconds before `Remove-Item`. Is there a measurably better pattern (e.g., named pipes) that's worth the complexity?
4. `git diff --cached --quiet` returns exit code 1 when there are staged changes — that's how we detect "anything to commit." This relies on the documented git behavior. Stable enough?
5. Any tests that you'd consider mandatory for a Phase 1 collector like this? My current view: a small `pytest` suite around `compute_relevance()`, `_fr_to_item()`, `_recall_to_item()` with frozen API fixtures would be a good Phase 2 addition. Phase 1 relies on the dry-run / live-run validation we just performed.

## How to leave feedback

- Inline comments on the PR (preferred — anchors to lines).
- High-level comment on the PR summary for architectural concerns.
- File a separate GitHub issue if the concern is a follow-up rather than a blocker.

Severity tags I will use when triaging:
- `must-fix`: blocks v15.0 production use
- `should-fix`: address before Phase 2
- `nice-to-have`: backlog
- `wontfix-by-design`: intentional, documented above

Thank you for the review.
