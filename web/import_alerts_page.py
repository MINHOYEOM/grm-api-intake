"""[수입경보 2단계 2026-11-10] FDA 수입경보 전체 목록 화면(/findings/import-alerts/) 뷰.

데이터: web/data/import_alerts.json(1단계 스냅샷, import_alerts_refresh.py — 로더는 render.load_import_alerts).
순수 함수만 둔다 — 네트워크·DB·now() 0(render.py 순수성 가드).

화면은 **등재 사실만** 옮긴다(경보 번호·업체명·국가·도시·등재일·품목 코드 수·상태). 원천에 위반
사유 서술이 없으므로 해석·사유 문구를 만들지 않는다. 주소·품목 설명은 스냅샷에 없다(FDA 원문 링크).

★국가 라벨: 수집기 국가 표기 → ISO2(grm_findings.normalize_country, 한국은 수집기 is_korea 우선) →
  한국어 이름(COUNTRY_LABELS_KO — findings.js 의 같은 이름 표와 동일 계약, 테스트가 대조). 매핑에
  없는 나라는 FDA 표기 그대로 보여 준다(추측 번역 금지). 영어 화면은 같은 한국어 키를 en.json 으로
  옮긴다(findings.js 가 이미 같은 키를 쓴다 — 키가 새로 생기지 않는다).
"""
from __future__ import annotations

import re
from typing import Any, Callable

import collect_fda_import_alert as _ia
import grm_findings as _gf

ROUTE = "findings/import-alerts/"
KOREA = "KR"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ALERT_RE = re.compile(r"^\d{2}-\d{2}$")
_HANGUL_RE = re.compile(r"[ㄱ-ㆎ가-힣]")

# ISO2 → 한국어 국가명. web/assets/findings.js COUNTRY_LABELS_KO 와 같은 표(68코드).
COUNTRY_LABELS_KO: dict[str, str] = {
    "US": "미국", "KR": "대한민국", "PR": "푸에르토리코", "IN": "인도", "CN": "중국",
    "JP": "일본", "DE": "독일", "CA": "캐나다", "FR": "프랑스", "GB": "영국",
    "IS": "아이슬란드", "IT": "이탈리아", "MY": "말레이시아", "ES": "스페인", "BE": "벨기에",
    "HU": "헝가리", "TW": "대만", "CH": "스위스", "CY": "키프로스", "AU": "호주",
    "IE": "아일랜드", "SE": "스웨덴", "JO": "요르단", "GR": "그리스", "DK": "덴마크",
    "NL": "네덜란드", "MX": "멕시코", "CZ": "체코", "LT": "리투아니아", "PL": "폴란드",
    "CL": "칠레", "AT": "오스트리아", "RO": "루마니아", "ZA": "남아프리카공화국",
    "BD": "방글라데시", "ID": "인도네시아", "LB": "레바논", "PT": "포르투갈",
    "SK": "슬로바키아", "LK": "스리랑카", "TR": "튀르키예", "NO": "노르웨이", "FI": "핀란드",
    "VN": "베트남", "BY": "벨라루스", "SI": "슬로베니아", "IL": "이스라엘",
    "SG": "싱가포르", "BR": "브라질", "TH": "태국", "MT": "몰타", "AR": "아르헨티나",
    "HR": "크로아티아", "HK": "홍콩", "CO": "콜롬비아", "NZ": "뉴질랜드", "BG": "불가리아",
    "DO": "도미니카공화국", "LV": "라트비아", "OM": "오만", "CR": "코스타리카", "EG": "이집트",
    "MO": "마카오", "PH": "필리핀", "UY": "우루과이", "AW": "아루바", "EE": "에스토니아",
    "AE": "아랍에미리트",
}

# FDA 가 붙인 경보 제목(원문 그대로 — 번역하지 않는다).
ALERT_TITLES: dict[str, str] = {
    "66-40": "Detention Without Physical Examination of Drugs From Firms Which Have Not Met Drug GMPs",
    "66-41": "Detention Without Physical Examination of Unapproved New Drugs Promoted In The U.S.",
}


def country_key(country: str) -> str:
    """필터 값. ISO2 가 있으면 그것, 없으면 수집기 정규화 표기(소문자·하이픈)에 'x-' 접두."""
    if _ia.is_korea(country):
        return KOREA
    code = _gf.normalize_country(country)
    if code:
        return code
    norm = _ia.normalize_country(country).lower().replace(" ", "-")
    return f"x-{norm}" if norm else ""


def alert_url(alert_no: str) -> str:
    alert = _ia.ALERTS.get(alert_no)
    return _ia.URL_TMPL.format(page_id=alert["page_id"]) if alert else ""


def list_view(snapshot: "dict[str, Any] | None", *, tr: Callable[..., str], lang: str,
              firm_slugs: "dict[str, str] | None" = None) -> "dict[str, Any] | None":
    """스냅샷 → 화면 뷰. 스냅샷이 없으면 None(페이지를 만들지 않는다).

    firm_slugs = {스냅샷 firm_key: 그 언어 트리에 실제로 선 업체 페이지 slug} — 있는 것만 링크.
    영어 트리(lang != "ko")에는 업체명·도시에 한글이 섞인 행을 싣지 않는다(1단계와 같은 규율).
    정렬: 등재 중 먼저 → 등재일 최신순 → 업체명 → 경보 번호.
    """
    if not snapshot:
        return None
    slugs = firm_slugs or {}
    rows: list[dict[str, Any]] = []
    for r in snapshot.get("listings") or []:
        if not isinstance(r, dict):
            continue
        alert_no = str(r.get("alert_no") or "")
        name = str(r.get("firm_name") or "").strip()
        country = str(r.get("country") or "").strip()
        if not (_ALERT_RE.match(alert_no) and name and country):
            continue
        city = str(r.get("city") or "").strip()
        if lang != "ko" and _HANGUL_RE.search(name + city):
            continue
        ckey = country_key(country)
        label_ko = COUNTRY_LABELS_KO.get(ckey)
        published = str(r.get("firm_published") or "")
        rows.append({
            "alert_no": alert_no,
            "firm_name": name,
            "city": city,
            "country_key": ckey,
            "country_label": tr(label_ko) if label_ko else country,
            "firm_published": published if _DATE_RE.match(published) else "",
            "n_products": len(r.get("product_codes") or []),
            "active": r.get("active") is True,
            "firm_slug": slugs.get(str(r.get("firm_key") or ""), ""),
        })
    rows.sort(key=lambda x: (not x["active"], _neg_date(x["firm_published"]),
                             x["firm_name"].casefold(), x["alert_no"]))

    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for x in rows:
        counts[x["country_key"]] = counts.get(x["country_key"], 0) + 1
        labels.setdefault(x["country_key"], x["country_label"])
    countries = [{"key": k, "label": labels[k], "n": n}
                 for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], labels[kv[0]]))]
    alerts = [{"alert_no": a, "url": alert_url(a), "title": ALERT_TITLES.get(a, ""),
               "n": sum(1 for x in rows if x["alert_no"] == a)}
              for a in sorted(_ia.ALERTS)]
    korea = [x for x in rows if x["country_key"] == KOREA]
    return {
        "measured_on": str(snapshot.get("measured_on") or ""),
        "rows": rows,
        "total": len(rows),
        "active": sum(1 for x in rows if x["active"]),
        "korea": len(korea),
        "korea_active": sum(1 for x in korea if x["active"]),
        "countries": countries,
        "alerts": alerts,
        "linked": sum(1 for x in rows if x["firm_slug"]),
    }


def _neg_date(d: str) -> str:
    """최신 날짜가 앞에 오도록 하는 정렬 키(빈 날짜는 맨 뒤)."""
    if not d:
        return "~"
    return "".join(chr(ord("9") - ord(ch) + ord("0")) if ch.isdigit() else ch for ch in d)
