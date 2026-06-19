# MFDS API Recovery Findings (2026-06-18)

## Summary

MFDS portal blocking on `www.mfds.go.kr` / `nedrug.mfds.go.kr` does not require a blanket KR-egress solution. Three official `apis.data.go.kr` paths are now wired as opt-in collectors:

| Track | Result | Collector | Flag |
|---|---|---|---|
| T1 law/admrul | Partial infrastructure-free recovery: official list/index API works through `apis.data.go.kr/1170000`; body-detail API still requires the direct `law.go.kr` DRF `OC`/registration path. | `collect_mfds_law.py` | `ENABLE_MFDS_LAW` |
| T2 GMP certificate/status | Infrastructure-free official status table via MFDS data.go.kr API. It is a certificate/status source, not a replacement for blocked nedrug GMP finding PDFs. | `collect_mfds_gmp_cert.py` | `ENABLE_MFDS_GMP_CERT` |
| T4 safety letters | Infrastructure-free official API found, contrary to the initial "KR IP required" hypothesis. | `collect_mfds_safety_letter.py` | `ENABLE_MFDS_SAFETY_LETTER` |

Local probes could confirm gateway reachability and operation existence, but this workspace has no `DATA_GO_KR_SERVICE_KEY`, so fetched>0 live dry-runs must be done in a key-bearing environment.

## Official Paths

### T1: MFDS Laws And Administrative Rules

- Official dataset: [data.go.kr 15000115, 법제처 국가법령정보 공유서비스](https://www.data.go.kr/data/15000115/openapi.do)
- List endpoint: `https://apis.data.go.kr/1170000/law/lawSearchList.do`
- Targets used: `target=admrul` and `target=law`
- MFDS filter: response `소관부처코드=1471000` or `소관부처명` containing `식품의약품안전처`
- Collector mapping:
  - `admrul` -> `notice-final`
  - `law` -> `regulation-final`
  - `Source Type=Official API`, `Language=KO`, `Region/Jurisdiction=Korea (MFDS)`

Important limitation: the official body-detail guide for administrative rules is the direct law.go.kr DRF endpoint `http://www.law.go.kr/DRF/lawService.do?target=admrul&OC=...`, documented at [open.law.go.kr](https://open.law.go.kr/LSO/openApi/guideResult.do?htmlName=admrulInfoGuide). A no-key probe reached the service but returned user-verification failure tied to registration. A probe of an analogous `apis.data.go.kr/1170000/law/lawService.do` detail path returned 404. Therefore this implementation stores list metadata and official detail links when present; it does not claim body text recovery.

### T2: MFDS GMP Certificate / Status

- Official dataset: [data.go.kr 15097207, 의약품 GMP 적합판정서 발급현황](https://www.data.go.kr/data/15097207/openapi.do)
- Endpoint: `https://apis.data.go.kr/1471000/DrugGmpStbltJgmtIssuStusService/getDrugGmpStbltJgmtIssuStusInq`
- Main fields: `BSSH_NM`, `FCTR_ADDR`, `KGMP_BGMP_NAME`, `GMP_INGR_MM_GROUP_NAME`, `VLD_PRD_YMD`
- Collector mapping:
  - `Type or Class=gmp-certificate`
  - `Signal Tier=Tier 1`
  - `Source Type=Official API`

This is a status/certificate inventory. It does not include GMP inspection findings or attachment text; the blocked `nedrug` inspection collector remains the richer finding source when available.

### T4: Safety Letters

- Official dataset: [data.go.kr 15059182, 의약품안전성서한 정보](https://www.data.go.kr/data/15059182/openapi.do)
- Endpoint: `https://apis.data.go.kr/1471000/DrugSafeLetterService02/getDrugSafeLetterList02`
- Main fields: `SAFT_LETT_NO`, `TITLE`, `PBANC_NO`, `PBANC_DIVS_CD`, `PBANC_DIVS_NM`, `PBANC_YMD`, `RLS_BGNG_YMD`, `SUMRY_CONT`, `PBANC_CONT`, `ACTN_MTTR_CONT`, `CHRG_DEP`, `ATTACH_FILE_URL`
- Collector mapping:
  - `Type or Class=safety-letter`
  - `Source Type=Official API`
  - Date from `PBANC_YMD` or `RLS_BGNG_YMD`
  - Official URL from `ATTACH_FILE_URL` when absolute, otherwise dataset page

This directly rebuts the initial hypothesis that safety letters necessarily require KR egress.

## Residuals

- T3 legislative notice: the current official data.go.kr listing still points to `lawmaking.go.kr/rest/ogLmPp` as a LINK-type service, not an `apis.data.go.kr/1170000` gateway. No clean KR-egress-free replacement was identified in this pass.
- Guidance/guideline boards (`data0011`, `data0013`, `data0010`): no general MFDS guidance/open-guide API equivalent was identified on data.go.kr or data.mfds.go.kr. Treat as residual until a specific official API is found.
- Law/admrul body text: official body detail exists, but through direct `law.go.kr` DRF with `OC`/registration, not the confirmed `apis.data.go.kr` gateway.

## Verification Status

- `apis.data.go.kr/1170000/law/lawSearchList.do` without key returned an authentication error, confirming the gateway path exists and is reachable.
- `apis.data.go.kr/1471000/DrugGmpStbltJgmtIssuStusService/getDrugGmpStbltJgmtIssuStusInq` without key returned an authentication error, confirming endpoint reachability.
- `apis.data.go.kr/1471000/DrugSafeLetterService02/getDrugSafeLetterList02` without key returned an authentication error, confirming endpoint reachability.
- Local unit tests use stubbed official responses for mapping, dedupe, key-missing, and scaffold behavior.

