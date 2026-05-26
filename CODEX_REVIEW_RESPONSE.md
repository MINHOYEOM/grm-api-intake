# Response to Codex Review (commit baseline: `95d212e`)

Self-contained record of which findings were addressed, how, and which were intentionally deferred. Lives alongside the code so a future reviewer (human or model) can see the audit trail.

## Severity triage

| Codex finding | Severity | Status | Notes |
|---|---|---|---|
| `http_get_json` / OpenFDA: API key leaks via retry & exception logs | must-fix | ✅ fixed | `safe_url = _mask_api_key(url)` used in every log line. `except RequestException` no longer includes `str(e)`. Final `RuntimeError` masks URL. |
| Notion insert failures don't fail workflow | must-fix | ✅ fixed | `insert_items` returns `(inserted, skipped, failed)`. `main()` exits 1 when `total_attempts > 0 and total_failed == total_attempts`. |
| Dedup query failure returns empty set → duplicate everything | must-fix | ✅ fixed | `notion_query_existing_doc_ids` now `raise RuntimeError(...) from e`. `main()` catches and exits 1 before any insert. |
| `window_days` shell interpolation | should-fix | ✅ fixed | Moved to `env.INPUT_WINDOW_DAYS`. Regex validation `^[0-9]+$` in shell. |
| `gh secret set --body $content` argv exposure | should-fix | ✅ fixed | PowerShell now starts `gh secret set` with redirected stdin and writes the exact secret via `.StandardInput.Write($value)`. No secret in argv and no pipeline-added trailing newline. |
| Cron `0 22 * * 0` top-of-hour drop risk | should-fix | ✅ fixed | Changed to `7 22 * * 0` (Sun 22:07 UTC = Mon 07:07 KST). Routine still has ~23 min buffer. |
| Failure issue title uses UTC date | should-fix | ✅ fixed | Computes `kstNow = Date.now() + 9h` and slices ISO. Title now `GRS Intake failure - {kstDate} KST - run #{runNumber}`. |
| OpenFDA 404 detection via string match | should-fix | ✅ fixed | Now `except requests.HTTPError as e: if e.response.status_code == 404`. Raw URL no longer in the matched string. |
| Retry policy treats all 4xx as transient | should-fix | ✅ fixed | 4xx (except 408 / 429) raise immediately as `requests.HTTPError` with masked URL. 5xx and network errors retry. |
| `git diff --cached --quiet` exit code 0/1/>1 | should-fix | ✅ fixed | Both `setup.ps1` and `setup.sh` branch on exit code 0 / 1 / other. >1 aborts with diagnostic. |
| Push failure must fail setup | should-fix | ✅ fixed | Tracked via `$script:PushSuccess` (PS) / `push_success` (bash). Both scripts now exit 1 after registering secrets if push failed. |
| FR pagination cap returns partial-success silently | nice-to-have | ✅ fixed | When `page_count > 10` we now `return items, "FR pagination cap reached..."` so `stats.fr_error` is set. |
| `PYTHONIOENCODING: utf-8` defensive | nice-to-have (Q1) | ✅ added | Added to workflow `env`. Harmless on `ubuntu-latest`, protects future runners. |
| LF/CRLF normalization | (not in review, observed) | ✅ added | New `.gitattributes`: `* text=auto eol=lf`. |
| Include `Source` in dedupe key | nice-to-have | ⏳ backlog | Filed as GitHub issue candidate. No actual collisions observed in first run. |
| Notion adapter: chunk JSON at line boundaries | wontfix-by-design | ✗ | Notion stores text chunks, not parsed JSON. Codex agrees. |
| `permissions: contents: read + issues: write` minimum | wontfix-by-design | ✗ | Codex agrees. No change. |
| `build_notion_properties` empty-string vs None | wontfix-by-design | ✗ | Codex agrees. No change. |
| Concurrency / single-run guarantee | wontfix-by-design | ✗ | Codex agrees. No change. |
| `timeout-minutes: 10` | wontfix-by-design | ✗ | Codex agrees. No change. |

## Q1 – Q5 explicit answers

### Q1. `PYTHONIOENCODING=utf-8` ?

Reflected. Codex: nice-to-have, not mandatory on `ubuntu-latest`, harmless. Set anyway for future-proofing if Windows/self-hosted runners enter the picture.

### Q2. Better secret-passing pattern than the temp file?

Reflected via redirected stdin. Codex's "stdin via `--body -`" works in bash (`setup.sh` already does `printf '%s' "$value" | gh secret set ... --body -`) because `printf '%s'` does not append a newline. In PowerShell, piping a string to a native command appends `[Environment]::NewLine`, which `gh secret set` persists into the stored secret value — a Notion token with `\r\n` appended breaks the Bearer header. PowerShell therefore uses `System.Diagnostics.ProcessStartInfo` with redirected stdin and `.StandardInput.Write($value)` so `gh` receives the secret without argv exposure or newline corruption.

### Q3. `git diff --cached --quiet` exit code semantics

Reflected. Both shell and PowerShell scripts now distinguish:
- `0` → no staged changes (warn, skip commit)
- `1` → staged changes present (commit)
- `>1` → git error (abort with diagnostic)

### Q4. Same-day re-run with new items

Confirmed Codex's view: desirable behavior. Newly available same-day items are inserted under the same Run Date because Document IDs differ. Documented operational rule for the Routine: it processes `Status = New`. No code change needed; dedup is by `(Run Date, Document ID)` and new IDs are not duplicates.

### Q5. Mandatory tests before scaling

Filed as a single Phase 2 issue (test-suite scaffolding). Concretely:
- `pytest` fixtures for `compute_relevance()`, `_fr_to_item()`, `_recall_to_item()`.
- Frozen KST/UTC boundary tests for `kst_run_date()` and `date_window()`.
- HTTP tests proving:
  - OpenFDA `api_key` redaction in logs and error messages.
  - 404 → empty results.
  - 429 / 5xx retry behavior, 4xx no-retry.
- Notion adapter tests for property shape, long-field truncation/chunking, optional-field omission.
- Idempotency tests with mocked Notion: existing IDs skipped, partial write failures retried, query/write failures fail closed.
- `window_days` input validation (positive integers only).

Deferred to Phase 2 because Phase 1 relies on the dry-run / live-run validation we already performed, and the test scaffolding is large enough to warrant its own PR.

## Files changed in this review pass

| File | Change |
|---|---|
| `collect_intake.py` | `http_get_json` retry + URL masking refactor; OpenFDA 404 via HTTPError; FR pagination cap surfaces error; `CollectionStats` adds `*_failed`; `insert_items` returns failed count; `notion_query_existing_doc_ids` fails closed; `main()` exits 1 on dedup fail or 100% sink fail; FR/Recall error messages masked. |
| `.github/workflows/grs-intake.yml` | Cron `0 22` → `7 22`. `workflow_dispatch` inputs moved to env vars + regex-validated. `PYTHONIOENCODING: utf-8` added. Failure issue title uses KST date + run number. |
| `setup.ps1` | `Set-RepoSecret` uses redirected stdin via `ProcessStartInfo` (no argv exposure, no trailing newline). `git diff --cached` exit code distinguished. `git push` failure propagates to setup exit code via `$script:PushSuccess`. |
| `setup.sh` | Same `git diff --cached` exit-code handling and push-failure propagation as PowerShell. Secrets path was already correct (`printf '%s' \| gh ... --body -`). |
| `.gitattributes` | New: `* text=auto eol=lf`. Eliminates Windows LF→CRLF warning noise. |
| `CODEX_REVIEW_RESPONSE.md` | This file. |

## Backlog (nice-to-have, filed for Phase 2)

1. Include `Source` in dedupe key to remove cross-source Document ID collision risk before scaling.
2. Build `pytest` suite as outlined in Q5.

## Validation performed

- `python3 -m py_compile collect_intake.py` → OK
- `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/grs-intake.yml'))"` → OK
- `bash -n setup.sh` → OK
- `setup.ps1` BOM byte check (`hexdump -C | head -1`) → `ef bb bf` confirmed; non-ASCII byte count = 3 (BOM only).
- `_mask_api_key` call sites: 6 (was 2).
- workflow `Run collector` env keys: `EVENT_NAME`, `INPUT_DRY_RUN`, `INPUT_WINDOW_DAYS`, `NOTION_DATABASE_ID`, `NOTION_TOKEN`, `OPENFDA_API_KEY`, `PYTHONIOENCODING`.

## Second-pass Codex verification

- PR opened: https://github.com/MINHOYEOM/grs-api-intake/pull/2
- Corrected during second pass: `gh secret set --body-file` is not supported by GitHub CLI 2.92.0. PowerShell secret registration now uses redirected stdin via `ProcessStartInfo`.
- Validation re-run:
  - `py -3 -m py_compile collect_intake.py` → OK
  - `npx --yes js-yaml .github/workflows/grs-intake.yml` → OK
  - `C:\Program Files\Git\bin\bash.exe -n setup.sh` → OK
  - `setup.ps1` parse check via `[scriptblock]::Create(...)` → OK
  - `gh secret set` redirected-stdin probe with `--no-store` → OK
  - `py -3 collect_intake.py --dry-run` → OK (12 FR + 3 recalls)
  - targeted probes for API-key redaction, retry/no-retry behavior, OpenFDA 404-as-empty, Notion insert failure counting, and dedup fail-closed → OK

## Original operator action

Push these changes to a new branch and open a PR for re-review:

```powershell
cd "C:\Users\user\Desktop\Global Regulatory Sweep\v15.0-implementation"
git checkout -b fix/codex-review-pass-1
git add collect_intake.py .github/workflows/grs-intake.yml setup.ps1 setup.sh .gitattributes CODEX_REVIEW_RESPONSE.md
git commit -m "fix(security,reliability): address Codex review pass 1 (must-fix x3, should-fix x7)"
git push -u origin fix/codex-review-pass-1
gh pr create --title "fix: Codex review pass 1 - security & reliability" --body-file CODEX_REVIEW_RESPONSE.md --base main --head fix/codex-review-pass-1
```

Then re-run Codex against the new commit and verify each must-fix / should-fix entry above is marked resolved on the second pass.

---

## Pass 3 — Routine prompt + Notion MCP limitation

Codex Pass 3 reviewed the areas not touched by Pass 1 / Pass 2: `GRS_Prompt_v15.0.md`,
the Notion MCP property-filter limitation handling (`PROMPT_REVIEW_PASS_1.md`), the
Notion schema doc, and `setup.ps1` parameter validation. Findings and resolutions:

### Pass 3 severity triage

| Codex finding | Severity | Status | Notes |
|---|---|---|---|
| PR mismatch / uncommitted scope | should-fix | ✅ resolved by this commit | Prompt-side fixes, doc updates, and review docs are now in the PR. |
| `INPUT_WINDOW_DAYS` allows `0` | nice→should | ✅ fixed | Regex tightened to `^[1-9][0-9]*$`. Error message reads `>= 1`. |
| `[0순위]` "WebFetch" still ambiguous | should-fix | ✅ fixed | Now explicitly "공식 사이트(fda.gov / federalregister.gov 등) 직접 WebFetch". Secondary-source WebFetch reference to `[3순위]` policy preserved. |
| `[0단계]` `notion-fetch` failure path underspecified | should-fix | ✅ fixed | Added: single fetch failure → skip + M2 record + continue. Total fetch failure → WebSearch-only graceful degradation with "Intake read failure: 0/{candidates} fetched" note. |
| `[0단계]` Status update timing unsafe | should-fix (critical) | ✅ fixed | Reordered: `Status = Processed` set **only after digest page creation completes**. Status update failure logged but does not roll back digest. Documented operational rule. |
| `[M2]/[M3]` metadata describes old read model | should-fix | ✅ fixed | M2 now reports `notion-search candidates`, `notion-fetch success/failure`, `client-side filter accepted/discarded`, and `Status 갱신 결과`. |
| `[M3]` label `API-WebSearch 불일치` stale | nice-to-have | ✅ fixed | Renamed to `Intake-WebSearch 불일치`. Description body unchanged. |
| `notion_intake_db_schema.md` "Routine 측 읽기 쿼리" stale | should-fix | ✅ fixed | Section rewritten to describe 3-step semantic search + per-row fetch + client-side filter flow, plus Status update timing rule. |
| `setup.ps1` `$RepoName` unconstrained | should-fix | ✅ fixed | Added `[ValidatePattern('^[A-Za-z0-9._-]+$')]` at param decl + re-validation after interactive prompt assignment (`-notmatch` check + `Fail-And-Exit`). Same pattern applied to `$Visibility`. |
| `.github/copilot-instructions.md` absent | nice-to-have | wontfix-by-design | Intentionally removed in the Copilot-cleanup turn. No future AI reviewer needs to inherit it; this audit trail (`CODEX_REVIEW_RESPONSE.md` + `PROMPT_REVIEW_PASS_1.md` + `CODEX_REVIEW_PASS_3_PROMPT.md`) covers the same role. |

### Q1' – Q3' answers reflected

- **Q1' (scale ceiling)**: Codex pegged ~50 rows/run as the practical fragility threshold,
  with redesign needed above 100. This becomes a Phase 2 KPI gate: redesign triggered by
  two consecutive runs > 50 candidates or any single run > 100. Recorded as backlog item;
  no code change in Phase 1.
- **Q2' (Status update mid-failure)**: Codex's recommendation (write digest first, then
  Status update; log failure, no rollback) is now codified in `GRS_Prompt_v15.0.md::0단계`
  "Status 갱신 타이밍" subsection.
- **Q3' (PowerShell argv escaping)**: Codex confirmed the secret itself is safe via stdin,
  but `$RepoName` flowed into hand-built `ProcessStartInfo.Arguments`. Fixed with
  `ValidatePattern` + interactive re-validation as described above.

### Pass 3 backlog (intentionally deferred)

1. Scale-driven redesign trigger (Q1' criteria) → Phase 2 issue when KPI gate hits.
2. pytest suite (carried over from Pass 1 Q5).
3. `Source` in dedupe key (carried over from Pass 1).

### Pass 3 validation performed

- `python3 -m py_compile collect_intake.py` → OK
- `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/grs-intake.yml'))"` → OK
- `bash -n setup.sh` → OK
- `setup.ps1` BOM byte check (`ef bb bf`) and non-ASCII byte count (3 = BOM only) → OK
- `_mask_api_key` call sites: 6 (unchanged from Pass 1).
- `Codex Pass 3` comment markers in `GRS_Prompt_v15.0.md`: 3 (D, E, F).
- `Intake-WebSearch 불일치` label appears in M3 instead of `API-WebSearch 불일치`.
- `notion-search` + `notion-fetch` keywords in schema doc: 5 occurrences (G fix).
- `RepoName` re-validation lines in `setup.ps1`: 2 (param + interactive).

### Pass 3 operator action

Push the Pass 3 fixes to the same branch (`fix/codex-review-pass-1`). PR #2 will auto-update.

```powershell
cd "C:\Users\user\Desktop\Global Regulatory Sweep\v15.0-implementation"
git add `
  .github/workflows/grs-intake.yml `
  GRS_Prompt_v15.0.md `
  README.md `
  setup_guide.md `
  notion_intake_db_schema.md `
  setup.ps1 `
  PROMPT_REVIEW_PASS_1.md `
  CODEX_REVIEW_PASS_3_PROMPT.md `
  CODEX_REVIEW_RESPONSE.md
git commit -m "fix(prompt,docs,setup): address Codex review pass 3 (8 should-fix + 1 nice-to-have)"
git push
```

Optionally re-run Codex with `CODEX_REVIEW_PASS_3_PROMPT.md` (effectively Pass 4 of Codex review) to verify the Pass 3 fixes are reflected and look for any remaining gaps before merging to main.
