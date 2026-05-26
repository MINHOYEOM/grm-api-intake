# Response to Codex Review (commit baseline: `95d212e`)

Self-contained record of which findings were addressed, how, and which were intentionally deferred. Lives alongside the code so a future reviewer (human or model) can see the audit trail.

## Severity triage

| Codex finding | Severity | Status | Notes |
|---|---|---|---|
| `http_get_json` / OpenFDA: API key leaks via retry & exception logs | must-fix | ✅ fixed | `safe_url = _mask_api_key(url)` used in every log line. `except RequestException` no longer includes `str(e)`. Final `RuntimeError` masks URL. |
| Notion insert failures don't fail workflow | must-fix | ✅ fixed | `insert_items` returns `(inserted, skipped, failed)`. `main()` exits 1 when `total_attempts > 0 and total_failed == total_attempts`. |
| Dedup query failure returns empty set → duplicate everything | must-fix | ✅ fixed | `notion_query_existing_doc_ids` now `raise RuntimeError(...) from e`. `main()` catches and exits 1 before any insert. |
| `window_days` shell interpolation | should-fix | ✅ fixed | Moved to `env.INPUT_WINDOW_DAYS`. Regex validation `^[0-9]+$` in shell. |
| `gh secret set --body $content` argv exposure | should-fix | ✅ fixed | Switched to `--body-file $tmp.FullName`. Temp file holds raw UTF-8 bytes (no BOM, no trailing newline) via `WriteAllBytes`. Path only enters argv. |
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

Reflected via `--body-file`. Codex's "stdin via `--body -`" works in bash (`setup.sh` already does `printf '%s' "$value" | gh secret set ... --body -`) because `printf '%s'` does not append a newline. In PowerShell, piping a string to a native command appends `[Environment]::NewLine`, which `gh secret set` persists into the stored secret value — a Notion token with `\r\n` appended breaks the Bearer header. `--body-file` avoids both argv exposure and the trailing-newline corruption without needing complex stdin redirection. Both transports never put the secret in argv.

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
| `setup.ps1` | `Set-RepoSecret` uses `--body-file` (no argv exposure, no trailing newline). `git diff --cached` exit code distinguished. `git push` failure propagates to setup exit code via `$script:PushSuccess`. |
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

## Pending operator action

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
