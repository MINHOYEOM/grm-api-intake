# MFDS KR-egress Setup Memo (2026-06-18)

## Scope

KR-egress is only for QA-critical MFDS/law.go.kr residuals that are not covered
by robust public APIs:

- GMP inspection deficiency bodies: `nedrug.mfds.go.kr`
- MFDS guidance/guideline RSS: `www.mfds.go.kr` boards `data0013`, `data0011`, `data0010`
- law.go.kr administrative-rule full text enrich: `www.law.go.kr`

API-backed sources remain the source of truth for recall, admin actions, safety
letters, GMP certificate status, and law/admrul lists.

## Environment

GitHub Actions secrets:

- `MFDS_HTTP_PROXY`: optional KR proxy URL, applied only to `www.mfds.go.kr`,
  `nedrug.mfds.go.kr`, and `www.law.go.kr`.
- `LAW_GO_KR_OC`: optional law.go.kr DRF OC for admrul body enrich.

GitHub repository variables:

- `MFDS_RSS_BOARD_MODE=residual` to fetch only guidance residual boards.
- `MFDS_RSS_BOARD_IDS` if an explicit board allowlist is preferred.

Local example:

```powershell
$env:MFDS_HTTP_PROXY = "http://user:pass@kr-proxy.example.com:3128"
$env:LAW_GO_KR_OC = "your-law-go-kr-oc"
py -3 probe_mfds_egress.py
```

## T-E0 Reachability Gate

Run from the candidate KR egress path before enabling scheduled collection:

```bash
python probe_mfds_egress.py
```

Acceptance criteria: all three probes return HTTP 200.

| Probe | URL | Required |
|---|---|---|
| MFDS guidance RSS | `https://www.mfds.go.kr/www/rss/brd.do?brdId=data0011` | HTTP 200 |
| nedrug GMP inspection list | `https://nedrug.mfds.go.kr/pbp/CCBBD03/getList?page=1&limit=10` | HTTP 200 |
| law.go.kr DRF | `https://www.law.go.kr/DRF/lawService.do` | HTTP 200 |

## Current Verification Result

Not run in this workspace: no candidate KR proxy/IP or `LAW_GO_KR_OC` was
provided. The code path is wired and covered by unit tests; fetched>0 validation
requires the operator-provided KR egress and keys.

| Probe | Result | Note |
|---|---|---|
| MFDS guidance RSS | Not run | needs KR egress candidate |
| nedrug GMP inspection list | Not run | needs KR egress candidate |
| law.go.kr DRF | Not run | needs KR egress candidate; body enrich also needs OC |

## Suggested Enablement Order

1. Configure a residential/company KR proxy if possible.
2. Run `probe_mfds_egress.py`; require three HTTP 200 results.
3. Set `MFDS_HTTP_PROXY` secret.
4. Set `MFDS_RSS_BOARD_MODE=residual` repository variable.
5. If law.go.kr body enrich is desired, set `LAW_GO_KR_OC`.
6. Run a workflow dry-run with `ENABLE_MFDS=true`,
   `ENABLE_MFDS_GMP_INSPECTION=true`, and `ENABLE_MFDS_LAW=true`.

Expected dry-run signals:

- GMP inspection fetched count is greater than 0.
- MFDS RSS residual guidance fetched count is greater than 0 when in-window items exist.
- MFDS law/admrul items keep list metadata even if body enrich fails; body enrich success
  adds `law_go_kr_body_excerpt` to raw payload.
