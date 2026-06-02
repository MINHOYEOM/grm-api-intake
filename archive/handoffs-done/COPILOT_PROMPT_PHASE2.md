# Copilot 의뢰 — GRM Phase 2 기능 추가 3건 (묶음 B)

> **대상 저장소**: `MINHOYEOM/grm-api-intake`
> **대상 파일**: `collect_intake.py`, `notion_intake_db_schema.md`
> **성격**: 버그 픽스가 아닌 기능 추가 + 데이터 품질 개선. 각 항목 독립적으로 적용 가능.
> **사전 조건**: 묶음 A (CODEX_PROMPT_BUGFIX.md) 적용 후 진행 권장.

---

## Feature 1 — QA Relevance 키워드 확장 + 단어 경계 오탐 수정

### 배경

`compute_relevance()` (L226) 는 `QA_CATEGORY_KEYWORDS` 리스트를 단순 `in` 연산자로 blob 전체에 매칭한다.
첫 실행(2026-05-26) 에서 수집된 15건 전부 `Pending` — 실제 OpenFDA 어휘(`dissolution`, `particulate`)가 기존 키워드에 없었기 때문.
또한 단순 `in` 매칭은 부분 문자열 오탐을 유발한다 (예: `"oos"` → `"Woods"`, `"csv"` → `"CSV file format"`, `"pqs"` → `"FAQs"`).

### 변경 1-A: 단어 경계 매칭으로 교체 (L236, L239)

현재:
```python
matches = sum(1 for kw in QA_CATEGORY_KEYWORDS if kw in blob)
...
boosts = sum(1 for kw in QA_LIKELY_BOOST if kw in blob)
```

수정 후:
```python
def _kw_match(blob: str, keywords: list[str]) -> int:
    """단어 경계 매칭. 단일 문자·약어는 \b, 복합어는 그대로."""
    count = 0
    for kw in keywords:
        pattern = r'\b' + re.escape(kw) + r'\b'
        if re.search(pattern, blob):
            count += 1
    return count

matches = _kw_match(blob, QA_CATEGORY_KEYWORDS)
...
boosts = _kw_match(blob, QA_LIKELY_BOOST)
```

`re`는 이미 import 되어 있음 (L30).
`_kw_match`는 모듈 수준 헬퍼로 `compute_relevance` 위에 정의.

### 변경 1-B: 리콜 특화 키워드 추가 (L79–100)

`QA_CATEGORY_KEYWORDS` 리스트에 아래를 추가 (기존 항목 변경 없이 append):
```python
# OpenFDA Recall 특화 — 경구 고형제 failure mode
"dissolution", "assay failure", "out of specification",
"particulate matter", "particulate contamination",
"subpotent", "superpotent", "mislabeling", "mislabelled",
"endotoxin",
# Nitrosamine 계열 (FDA hot topic)
"nitrosamine", "ndma", "ndea", "n-nitroso",
# 외국 제조사 학습 가치 (generic 경쟁사)
"alkem", "aurobindo", "lupin", "dr. reddy", "dr reddy", "zydus",
```

`QA_LIKELY_BOOST` 리스트에 추가:
```python
"dissolution failure", "failed dissolution",
"nitrosamine impurity", "ndma impurity",
```

> **주의**: `"india"` 키워드는 false positive 비율이 높아 제외. 제조사명으로만 특정.

### 변경 1-C: `QA_EXCLUDE_KEYWORDS` 단어 경계 적용

`compute_relevance` L230:
```python
# 현재
if any(ex in blob for ex in QA_EXCLUDE_KEYWORDS):
# 수정
if any(re.search(r'\b' + re.escape(ex) + r'\b', blob) for ex in QA_EXCLUDE_KEYWORDS):
```

---

## Feature 2 — OSD (경구 고형제) Signal 추가

### 배경

OpenFDA Recall raw payload에는 이미 OSD 분류에 필요한 데이터가 있다:
- `openfda.route`: `["ORAL"]`
- `openfda.dosage_form`: `["TABLET", "CAPSULE", "EXTENDED-RELEASE TABLET", ...]`
- `openfda.application_number`: `["ANDA211143"]` (generic 여부)

이를 Notion Intake DB에 별도 속성으로 저장해 두면, Claude Routine이 OSD 관련 항목을 즉시 식별할 수 있다.

### 변경 2-A: 상수 추가 (L57 이후 상수 블록)

```python
PROP_OSD_RELEVANCE = "OSD Relevance"   # select: Direct / Indirect / N/A

OSD_ROUTES = {"oral"}
OSD_FORMS = {
    "tablet", "capsule", "extended-release tablet", "er tablet",
    "delayed-release tablet", "chewable tablet", "orally disintegrating tablet",
    "powder for oral solution", "oral solution", "oral suspension",
}
```

### 변경 2-B: OSD 분류 함수 추가 (Feature 1 헬퍼 아래)

```python
def compute_osd_relevance(raw_payload: dict[str, Any]) -> str:
    """
    OpenFDA raw payload 에서 OSD 직접 관련성 판정.
    Returns: "Direct" | "Indirect" | "N/A"
    """
    openfda = raw_payload.get("openfda") or {}
    routes = {r.lower() for r in (openfda.get("route") or [])}
    forms = {f.lower() for f in (openfda.get("dosage_form") or [])}

    if routes & OSD_ROUTES or forms & OSD_FORMS:
        return "Direct"

    # openfda 필드가 비어있어도 product_description 에서 단서 탐색
    product = (raw_payload.get("product_description") or "").lower()
    if any(f in product for f in ["tablet", "capsule", "oral"]):
        return "Indirect"

    return "N/A"
```

### 변경 2-C: `IntakeItem` 데이터클래스에 필드 추가 (L126)

```python
@dataclass
class IntakeItem:
    ...
    osd_relevance: str = "N/A"   # "Direct" | "Indirect" | "N/A" — Recall 전용, FR은 N/A
```

### 변경 2-D: `_recall_to_item()` 에서 OSD 분류 호출 (L362)

`_recall_to_item` 함수 내 `return IntakeItem(...)` 직전에:
```python
osd_rel = compute_osd_relevance(r)
```

`IntakeItem(...)` 생성 시 `osd_relevance=osd_rel` 추가.

FR 항목(`_fr_to_item`)은 `osd_relevance` 미설정 (기본값 `"N/A"`).

### 변경 2-E: `build_notion_properties()` 에 OSD Relevance 속성 추가

Notion `select` 타입:
```python
if item.osd_relevance and item.osd_relevance != "N/A":
    props[PROP_OSD_RELEVANCE] = {"select": {"name": item.osd_relevance}}
else:
    props[PROP_OSD_RELEVANCE] = {"select": {"name": "N/A"}}
```

> **Notion DB 선행 작업 (사용자)**: Notion에서 `GRM API Intake` DB에 `OSD Relevance` select 속성 수동 추가 필요. 옵션: `Direct`, `Indirect`, `N/A`. 코드 적용 전에 먼저 추가할 것.

---

## Feature 3 — 날짜 유효성 검증 강화

### 배경

`_fr_to_item` (L283) 는 FR API의 `publication_date` 필드를 검증 없이 Notion에 전달한다.
`_recall_to_item` (L362) 의 YYYYMMDD → YYYY-MM-DD 변환 fallback (L377) 은 비정상 문자열이 그대로 넘어갈 수 있다.
Notion API는 잘못된 날짜 문자열에 400을 반환하고 해당 row가 silent 누락된다.

### 변경: `_date_iso()` 헬퍼 추가 또는 보강

기존에 `_date_iso` 함수가 없다면 신규 추가, 있다면 보강:

```python
def _safe_date_iso(value: str, context: str = "") -> str:
    """
    날짜 문자열 검증. 유효하면 YYYY-MM-DD 반환, 실패하면 "" 반환 + WARN.
    YYYYMMDD 포맷도 자동 변환.
    """
    if not value:
        return ""
    # YYYYMMDD → YYYY-MM-DD 변환
    if len(value) == 8 and value.isdigit():
        value = f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    try:
        datetime.fromisoformat(value)
        return value
    except ValueError:
        log("WARN", f"날짜 파싱 실패 (context={context}): {value!r} → 빈 문자열로 처리")
        return ""
```

`_fr_to_item` (L286) 수정:
```python
pub = _safe_date_iso((r.get("publication_date") or "").strip(), context=f"FR/{doc_id}")
```

`_recall_to_item` (L373–377) 의 report_date 처리를 `_safe_date_iso`로 교체:
```python
date_iso = _safe_date_iso(report_date_raw, context=f"Recall/{recall_number}")
```

---

## 스키마 문서 정합성 수정 (notion_intake_db_schema.md)

### 배경

`notion_intake_db_schema.md` 의 13개 카테고리 키워드 목록이 `collect_intake.py`의 `QA_CATEGORY_KEYWORDS` 상수와 불일치한다. 스키마 문서를 읽는 사람이 코드 동작을 오해한다.

### 수정 방향

`notion_intake_db_schema.md` 의 QA Relevance 키워드 설명 섹션에 아래 문구 추가:

```
> **단일 진실 공급원**: 실제 매칭 키워드 목록은 `collect_intake.py`의
> `QA_CATEGORY_KEYWORDS` 및 `QA_LIKELY_BOOST` 상수가 기준입니다.
> 이 문서의 키워드 목록은 참고용이며, 코드 변경 시 이 섹션도 함께 갱신하세요.
```

그리고 Feature 1에서 추가한 새 키워드들을 문서 키워드 목록에도 반영.

또한 `OSD Relevance` 속성을 17번째 속성으로 스키마 표에 추가:
```
| 17 | OSD Relevance | select | Direct / Indirect / N/A | Recall 항목만. FR은 N/A 기본. |
```

---

## 커밋 분리 제안

```
feat(relevance): word-boundary matching + recall-specific keyword expansion
feat(osd): OSD Relevance field in IntakeItem, _recall_to_item, Notion property
fix(date): _safe_date_iso() validator for FR + Recall date fields
docs(schema): align keyword list with code, add OSD Relevance property
```

---

## 검증 체크리스트

- [ ] `compute_relevance("", "Dissolution failure detected", "ORAL TABLET")` → `"Likely"` 반환
- [ ] `compute_relevance("", "submitted as a CSV file format", "")` → `"Pending"` (오탐 방지 확인)
- [ ] `compute_osd_relevance({"openfda": {"route": ["ORAL"], "dosage_form": ["TABLET"]}})` → `"Direct"`
- [ ] `compute_osd_relevance({"product_description": "injection solution"})` → `"N/A"`
- [ ] `_safe_date_iso("20260526")` → `"2026-05-26"`
- [ ] `_safe_date_iso("2026-13-01")` → `""` + WARN 로그 출력
- [ ] `_safe_date_iso("")` → `""`
- [ ] dry-run 모드에서 `OSD Relevance` 값이 stdout에 출력되는지 확인
