# Codex re-review prompt (pass 3)

> Codex (CLI / ChatGPT) 의 새 세션에 아래 블록 전체를 그대로 붙여넣으세요.
>
> Codex 의 review 이력:
> - **Pass 1** (commit `95d212e`): 최초 broad review. must-fix ×3 + should-fix ×7 + Q1-Q5 식별. 모두 반영.
> - **Pass 2** (commit `8e3d7c7`): PR #2 verification. `gh secret set --body-file` 가 GitHub CLI 2.92.0 에서 지원되지 않는 것을 발견하고 `ProcessStartInfo` + `RedirectStandardInput` 로 교정. 그 외 must-fix / should-fix 없음.
> - **Pass 3** (이 prompt): pass 1·2 가 손대지 않은 새 영역 (Routine 프롬프트 자체 + Notion MCP 한계 처리) 검토 요청.

```
You are reviewing the public repository at https://github.com/MINHOYEOM/grs-api-intake
on branch `fix/codex-review-pass-1` (PR #2). This is the THIRD review pass.

Your earlier passes:
- Pass 1 (baseline 95d212e): identified must-fix x3 + should-fix x7 + answered Q1-Q5.
  All findings addressed, documented in `CODEX_REVIEW_RESPONSE.md`.
- Pass 2 (baseline 8e3d7c7): PR #2 verification. You caught and fixed `gh secret set
  --body-file` not being supported by GitHub CLI 2.92.0; replaced with
  `ProcessStartInfo` + `RedirectStandardInput`. No remaining must-fix / should-fix in
  that pass.

This Pass 3 covers areas your earlier passes did NOT touch: the Routine prompt itself
(`GRS_Prompt_v15.0.md`) and the Notion MCP property-filter limitation. See
`PROMPT_REVIEW_PASS_1.md` for the self-review record that prompted these changes.

──────────  PROJECT CONTEXT (read this first if you don't already have it)  ──────────

Single user: a QA professional at a Korean pharmaceutical company that manufactures
oral solid dosage forms (tablets). Korean domestic regulatory affairs are handled by a
separate RA team, so this tool focuses only on GLOBAL GMP/CGMP regulatory changes
(FDA, EMA, PIC/S, ICH, MHRA, Health Canada, TGA, PMDA, HSA).

Architecture:
  [GitHub Actions: Sun 22:07 UTC = Mon 07:07 KST]
       └─ python collect_intake.py
             ├─ Federal Register API (no key)
             └─ OpenFDA Drug Enforcement API (free key optional)
       └─ Notion "GRS API Intake" DB (raw API fields preserved)
  [Claude Code Routine: Mon 07:30 KST, separate system]
       └─ reads Notion intake, runs WebSearch supplemental, produces curated digest

First production run on 2026-05-26 KST: 12 FR + 3 Recalls inserted, 0 errors.

──────────  WHAT WAS CHANGED SINCE YOUR PASS 1 REVIEW  ──────────

See `CODEX_REVIEW_RESPONSE.md` for full audit. Highlights:

- `http_get_json`: URL masking on every path, 4xx (non-retryable except 408/429) vs 5xx
  retry policy split, no `str(e)` in logs.
- `notion_query_existing_doc_ids`: now raises RuntimeError (fail-closed); main() exits 1.
- `insert_items`: returns (inserted, skipped, failed); main() exits 1 on 100% sink failure.
- Workflow: cron `0 22 * * 0` → `7 22 * * 0`. workflow_dispatch inputs moved to env vars
  with `^[0-9]+$` regex validation. PYTHONIOENCODING=utf-8. KST issue title + run number.
- setup.ps1: secret via ProcessStartInfo + RedirectStandardInput (no argv, no newline
  corruption). git diff --cached exit 0/1/>1 distinguished. push failure propagates.
- setup.sh: same git diff + push failure propagation.
- .gitattributes: LF normalization.

──────────  WHAT IS NEW IN THIS PASS  ──────────

This pass also covers TWO areas your first review did NOT touch:

(A) `GRS_Prompt_v15.0.md` — the 33KB Claude Code Routine prompt. I performed a
self-review and fixed 6 internal-consistency gaps:

F1 (critical) - 핵심 원칙 6 said "best-effort 1회 시도" while [0순위] said "do not
attempt"; LLM ambiguity. Reconciled: Routine does not attempt official-API direct
calls or official-site direct WebFetch. Secondary-source WebFetch ([3순위]) is the
ONLY WebFetch policy now.

F2 (critical) - cron drift: "22:00 UTC / 07:00 KST" still in 4 files after the
cron change. Updated to "22:07 UTC / 07:07 KST" everywhere.

F3 (moderate) - "WebFetch" was the same word for two different policies (official
fetch = forbidden; secondary fetch = allowed). Now explicit in 핵심 원칙 6.

F4 (moderate) - `QA Relevance = Unrelated` handling was unspecified. Now explicit:
"verify by reading raw payload's reason_for_recall / abstract; include if relevant,
log to M2 if not."

F5 (moderate) - `Status` field update was undocumented. Now: "set Status = Processed
on rows whose cards were written; via Notion API update."

F6 (critical) - **Notion MCP limitation**. The Notion MCP exposes only `notion-search`
(semantic) and `notion-fetch` (single entity); there is NO query_data_sources tool
with user-property filters. My empirical test:

    notion-search(data_source_url=..., query="Run Date 2026-06-01", page_size=5)
    → returns rows whose actual Run Date is 2026-05-26

i.e. semantic ranking can bring in rows from other Run Dates. The previous prompt
trusted notion-search to be a strict filter — that's incorrect. The new prompt
mandates a 3-step flow:
  1) notion-search returns candidates
  2) notion-fetch on each candidate to read properties
  3) client-side check: Run Date == today_kst AND Source in {FR, Recall} AND Status
     in {New, Processed}

See `PROMPT_REVIEW_PASS_1.md` for the full self-review record and `GRS_Prompt_v15.0.md`
for the updated prompt body (section [0단계 — Notion Intake 읽기], "실행 흐름" subsection).

(B) Documentation drift: `README.md`, `setup_guide.md`, and prompt-internal cron
references updated to 22:07. `setup_guide.md` security note clarified to reflect the
PowerShell ProcessStartInfo path.

──────────  WHAT I WANT FROM THIS REVIEW  ──────────

1. CONFIRM pass-1 fixes are correct
   - `http_get_json` masked-URL and retry split: any 4xx not in {408, 429} still
     raises HTTPError with masked URL?
   - notion_query_existing_doc_ids: confirm fail-closed propagates to exit 1 with no
     attempt to insert.
   - insert_items / main(): if Notion is 100% down for a non-dry-run with at least
     one source API success, does the workflow now exit 1? Confirm via reading.
   - workflow inputs: `INPUT_WINDOW_DAYS` validation rejects non-digit input?
   - setup.ps1 ProcessStartInfo path: confirm secret value never appears in
     `psi.Arguments`, only in `StandardInput.Write($value)`.
   - setup.sh git diff exit handling: branches on 0 / 1 / other?

2. REVIEW the prompt-side changes (NEW, not in pass 1)
   - F1: is the new wording in 핵심 원칙 6 unambiguous? Could a LLM still interpret
     it as "try once"?
   - F6: is the client-side property verification flow specified precisely enough
     that the Routine LLM will actually do it? Are there error paths I missed
     (e.g., notion-fetch returns 404 for a row that existed at notion-search time)?
   - F4 + F5 + Status update: is the operational sequence (read → write digest →
     update Status) safe under partial-failure? E.g., what if Status update fails
     after the digest was written?

3. FIND new gaps in areas I haven't asked about yet
   - Anything in `collect_intake.py` that pass-1 fixes might have regressed.
   - Anything in `GRS_Prompt_v15.0.md` blocks 11~13 (digest layout) that doesn't
     match the Intake-first reality.
   - `.github/copilot-instructions.md` accuracy after the changes.
   - `notion_intake_db_schema.md` accuracy (the heuristic block uses
     `ICH/CGMP terminology`; with first-run evidence that this is too narrow, is the
     doc misleading future maintainers? Pass-1 you flagged this as wontfix; reaffirm
     or revise.)
   - Any operational gap that would surface on the next scheduled run
     (Sun 2026-05-31 22:07 UTC = Mon 2026-06-01 07:07 KST).

4. ANSWER three new questions (Q1'-Q3')

Q1'. The new F6 flow does notion-search + notion-fetch per row. For 15-20 rows that's
~16-21 round-trips. At what scale (rows/run) would this break, and what's a sensible
threshold above which the architecture should change?

Q2'. The Status field is updated to "Processed" by the Routine. If the Routine fails
midway (e.g., between processing row 5 and row 6), some rows will have Status =
Processed and others Status = New. Next Monday's Routine query will return both. Is
this a desirable state, or should we add an idempotency token (e.g., Status =
"In progress (YYYY-MM-DD)") with two-phase commit?

Q3'. `setup.ps1` uses `System.Diagnostics.ProcessStartInfo` with positional
`Arguments` (built by string concatenation). Is there a Windows PowerShell 5.1
escaping pitfall I'm missing (e.g., if `$RepoName` contained a quote character)?
The current validation chain is: param ValidateSet for Visibility, no validation for
RepoName. Should I add `[ValidatePattern('^[A-Za-z0-9._-]+$')]` to $RepoName?

──────────  HOW TO REPLY  ──────────

For each finding, severity tag (must-fix / should-fix / nice-to-have / wontfix-by-design).
Inline code references like `collect_intake.py::http_get_json:222-251`.
Answer Q1'-Q3' explicitly.
No flattery, no "consider", direct findings only.
```
