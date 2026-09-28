#!/usr/bin/env python3
"""GMP 규제 용어집 PDF — glossary.json → A4 HTML → 헤드리스 Chrome PDF (마케팅 2026-09-28).

구독 확인 메일의 버튼 → `/welcome/` 도착 페이지 → 이 PDF. 구독해야 받는 자료라
PDF 주소는 사이트 어디에도 링크하지 않고(_headers 가 noindex), 배포 때마다 새로 만든다 —
용어가 더해지면 다음 배포에서 새 판이 된다(표지의 판 날짜·개수도 그때 다시 센다).

구성: 표지 → 이 용어집·GRM 소개(QR) → 전체 색인 → 초성별 본문(용어마다 '온라인에서 보기')
→ 뒤표지(QR). 본문 사이에 GRM 기능 안내 네 칸. QR 은 UTM 을 달아 "용어집을 보고 들어와
구독했다"를 first-touch(087)로 셀 수 있게 한다.

재사용: 용어 뷰모델·사례 수는 `render`(색인 페이지와 같은 정렬·같은 값), 브랜드 부엉이·
폰트·개념 그림·Chrome 렌더러는 `linkedin_cards`(카드뉴스와 같은 모양). 값은 무변형 —
표제어·풀이·출처를 고쳐 쓰지 않는다.

실행: `python web/glossary_pdf.py --out web/dist` (배포 워크플로 비차단 스텝).
Chrome 이 없으면 HTML 만 남기고 종료 코드 2. PDF 를 만든 뒤 PyMuPDF 로 읽어 용어가 전부
실렸는지 확인하고, 모자라면 종료 코드 1(배포는 계속 — site_probe 가 라이브 주소를 본다).
"""
from __future__ import annotations

import argparse
import html
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import linkedin_cards as lc  # noqa: E402
import render  # noqa: E402
from utm import with_utm  # noqa: E402

# 배포되는 PDF 는 프리뷰 배포에서 만들어져도 운영 주소만 가리켜야 한다 — env 로 바뀌는
# render.SITE_BASE_URL 대신 고정한다(표시 문자열도 전부 grm-solutions.com 이다).
SITE = "https://grm-solutions.com"
KST = timezone(timedelta(hours=9))
UTM = ("glossary_pdf", "pdf", "glossary_pdf")
QR_GLOSSARY = with_utm(SITE + "/glossary/", *UTM)
QR_HOME = with_utm(SITE + "/", *UTM)
ONLINE = "온라인에서 보기 →"

# 본문 사이 GRM 안내 — (제목, 설명, 표시 주소). 네 칸을 초성 묶음 사이에 고르게 끼운다.
PROMOS = (
    ("이 용어가 실제로 지적된 문장", "FDA 483·경고서한, EU·영국 GMP 비준수, 식약처 행정처분을 한국어로 검색하세요.", "grm-solutions.com/findings"),
    ("매주 월요일, 규제 소식 한 장", "FDA·EMA·MHRA·PIC/S·식약처 소식을 핵심 사실·시사점·점검 포인트로 정리해 보내드립니다.", "grm-solutions.com"),
    ("요즘 어떤 지적이 늘고 있나", "기관별·분야별 최근 지적 흐름을 한 화면에서 봅니다.", "grm-solutions.com/findings/trends"),
    ("3분 규제 퀴즈", "이번 주 소식과 용어로 만든 짧은 퀴즈로 확인해 보세요.", "grm-solutions.com/quiz"),
)
TILES = (
    ("주간 규제 브리프", "매주 월요일, 그 주의 규제 소식을 핵심 사실·시사점·점검 포인트로 정리합니다. 뉴스레터로 받아볼 수 있습니다.", "grm-solutions.com"),
    ("실사 지적사항 검색", "FDA 483·경고서한, EU·영국 GMP 비준수, 식약처 행정처분의 지적 문장을 한국어로 찾아봅니다.", "grm-solutions.com/findings"),
    ("규제 동향", "최근 지적이 어느 분야에 몰리는지, 기관별로 무엇이 늘었는지 봅니다.", "grm-solutions.com/findings/trends"),
    ("주간 퀴즈", "이번 주 소식과 규제·품질 용어로 만든 짧은 퀴즈. 3분이면 충분합니다.", "grm-solutions.com/quiz"),
)


def _e(s: Any) -> str:
    return html.escape(str(s or ""), quote=True)


def _href(shown: str) -> str:
    """표시 주소(grm-solutions.com/…) → 실제 링크(https://…/). 표시와 링크가 한 값에서 나온다."""
    path = shown[len("grm-solutions.com"):].strip("/")
    return f"{SITE}/{path}/" if path else f"{SITE}/"


def qr_svg(url: str, dark: str = "#141413") -> str:
    import segno  # 지연 import — 모듈 import 만으로 의존성을 요구하지 않는다
    return segno.make(url, error="m").svg_inline(scale=4, border=0, dark=dark, light=None)


CSS = """
@page{size:A4;margin:15mm 15mm 17mm;
  @bottom-left{content:"GRM · GMP 규제 용어집";font:500 7.5pt 'Pretendard',sans-serif;color:#8E8B82}
  @bottom-right{content:"grm-solutions.com/glossary   " counter(page);font:500 7.5pt 'Pretendard',sans-serif;color:#8E8B82}}
@page full{margin:0;@bottom-left{content:none}@bottom-right{content:none}}
*{box-sizing:border-box}
html,body{margin:0;background:#fff;-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{font-family:'Pretendard',-apple-system,'Segoe UI',sans-serif;color:#141413;font-size:9.4pt;line-height:1.6;word-break:keep-all;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
.full{page:full;width:210mm;height:297mm;position:relative;overflow:hidden;break-after:page;display:flex;flex-direction:column;padding:22mm 20mm 18mm}
.coral{background:#C2603F url(HEX_CREAM) 0 0/26mm 45mm;color:#FAF9F5}
.dark{background:#1A1815 url(HEX_DARK) 0 0/26mm 45mm;color:#FAF9F5}
.brand{display:flex;align-items:center;gap:4mm}
.brand svg{width:13mm;height:13mm;border-radius:3mm;display:block}
.brand .w{display:flex;flex-direction:column;line-height:1.05}
.brand .w b{font-family:'Noto Serif KR',Georgia,serif;font-weight:600;font-size:19pt}
.brand .w span{font-size:9pt;opacity:.82;margin-top:1.2mm}
.cv-main{flex:1;display:flex;flex-direction:column;justify-content:center}
.eyebrow{display:flex;align-items:center;gap:3mm;font-weight:700;font-size:12pt;margin-bottom:7mm}
.eyebrow::before{content:'';width:8mm;height:1mm;background:currentColor;border-radius:1mm}
.cv-h1{margin:0;font-size:44pt;line-height:1.12;font-weight:800}
.cv-sub{margin:7mm 0 0;font-size:13pt;opacity:.9}
.cv-stats{display:flex;gap:4mm;margin-top:14mm}
.cv-stat{flex:1;border:.4mm solid rgba(250,249,245,.35);background:rgba(250,249,245,.1);border-radius:4mm;padding:5mm 5mm 4mm}
.cv-stat b{display:block;font-size:26pt;line-height:1}
.cv-stat span{display:block;margin-top:2mm;font-size:9pt;opacity:.88}
.cv-ft{display:flex;justify-content:space-between;align-items:flex-end;font-size:9.5pt;opacity:.9}
.cv-ft b{font-size:11pt}
h2.sec{font-size:15pt;margin:0 0 4mm;display:flex;align-items:center;gap:3mm}
h2.sec::before{content:'';width:7mm;height:1mm;background:#C2603F;border-radius:1mm}
.intro{break-after:page}
.intro ul{margin:0 0 9mm;padding-left:5mm}
.intro li{margin:0 0 2mm}
.more{display:flex;align-items:center;gap:6mm;margin:0 0 8mm;padding:5mm 6mm;border-radius:3.5mm;background:#FBF3EE;border:.3mm solid #F0D9CC}
.more .tx b{display:block;font-size:12pt;margin-bottom:1.5mm}
.more .tx p{margin:0;font-size:9pt;color:#3D3D3A}
.more .tx a{display:inline-block;margin-top:2.5mm;font-weight:700;color:#A14B30;font-size:10pt}
.qr{flex:none;width:26mm;height:26mm;background:#fff;border-radius:2.5mm;padding:2.2mm}
.qr svg{width:100%;height:100%;display:block}
.tag{font-family:'Noto Serif KR',Georgia,serif;font-size:21pt;font-weight:600;line-height:1.3;margin:2mm 0 3mm}
.tag em{font-style:normal;color:#C2603F}
.lead{color:#3D3D3A;margin:0 0 6mm}
.tiles{display:grid;grid-template-columns:1fr 1fr;gap:4mm;margin-bottom:8mm}
.tile{border:.3mm solid #E6DFD8;border-radius:3.5mm;padding:4.5mm 5mm;background:#FFFDF9}
.tile b{display:block;font-size:11pt;margin-bottom:1.2mm}
.tile p{margin:0;color:#3D3D3A;font-size:9pt;line-height:1.55}
.tile .u{display:block;margin-top:2.5mm;color:#A14B30;font-weight:600;font-size:8.6pt}
.note{font-size:8.2pt;color:#6C6A64;border-top:.3mm solid #E6DFD8;padding-top:3mm}
.idx{break-after:page}
.idx-g{break-inside:avoid;margin-bottom:3.5mm}
.idx-g h3{margin:0 0 1.2mm;font-size:12pt;color:#C2603F}
.idx-g ul{margin:0;padding:0;list-style:none;columns:3;column-gap:6mm;font-size:8.6pt;line-height:1.55}
.idx-g li{break-inside:avoid;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.grp{break-before:page}
.grp-h{font-size:30pt;font-weight:800;color:#C2603F;margin:0 0 3mm;line-height:1;break-after:avoid;border-bottom:.5mm solid #C2603F;padding-bottom:2mm}
.t{break-inside:avoid;padding:3.6mm 0 3.8mm;border-bottom:.3mm solid #EFE9DE;display:flow-root}
.t .fig{float:right;width:38mm;height:22.3mm;margin:1mm 0 1mm 5mm;color:#141413}
.t .fig svg{width:100%;height:100%;display:block}
.t h4{margin:0;font-size:11.6pt;line-height:1.35}
.t h4 .en{font-weight:500;color:#6C6A64;font-size:9pt;margin-left:1.5mm}
.t .easy{margin:1.4mm 0 0;font-size:9.6pt}
.t .detail{margin:1.4mm 0 0;font-size:8.8pt;color:#3D3D3A}
.t .meta{margin:1.8mm 0 0;font-size:7.8pt;color:#6C6A64;line-height:1.5}
.t .meta b{color:#3D3D3A;font-weight:600}
.t .online{display:inline-block;margin-top:1.8mm;font-size:7.9pt;font-weight:600;color:#A14B30;background:#FBF3EE;border-radius:1.5mm;padding:.6mm 2.2mm}
.promo{break-inside:avoid;margin:6mm 0 2mm;border-radius:3.5mm;background:#FBF3EE;border:.3mm solid #F0D9CC;padding:4.5mm 5mm;display:flex;align-items:center;gap:4.5mm}
.promo svg{width:11mm;height:11mm;border-radius:2.6mm;flex:none;display:block}
.promo b{display:block;font-size:10.6pt;margin-bottom:.8mm}
.promo p{margin:0;font-size:8.8pt;color:#3D3D3A}
.promo .u{margin-left:auto;flex:none;font-weight:700;color:#A14B30;font-size:9pt}
.bk-main{flex:1;display:flex;flex-direction:column;justify-content:center}
.bk-h{font-family:'Noto Serif KR',Georgia,serif;font-size:34pt;font-weight:600;line-height:1.25;margin:0}
.bk-h em{font-style:normal;color:#E8A98F}
.bk-p{font-size:12.5pt;margin:7mm 0 0;opacity:.9}
.bk-list{margin:9mm 0 0;padding:0;list-style:none;font-size:10.5pt;opacity:.92}
.bk-list li{margin:0 0 2mm}
.bk-qr{display:flex;align-items:center;gap:6mm;margin-top:10mm}
.bk-qr .qr{width:30mm;height:30mm;background:#FAF9F5}
.bk-qr .tx{font-size:10.5pt;opacity:.92;line-height:1.6}
.bk-qr .tx b{display:block;font-size:12.5pt;margin-bottom:1mm}
.bk-ft{font-size:8pt;opacity:.75;line-height:1.6}
.mini text{font-family:'Pretendard',sans-serif}
"""


def _brand(icon: str) -> str:
    return (f'<div class="brand">{icon}<span class="w"><b>GRM</b>'
            f'<span>Global Regulatory Monitor</span></span></div>')


def _case_count(t: dict[str, Any]) -> int:
    return render._glossary_case_count({"findings": t.get("case_findings")})


def cover(n_terms: int, n_figs: int, n_cases: int, edition: str) -> str:
    return (f'<section class="full coral">{_brand(lc.OWL_CREAM_SVG)}'
            '<div class="cv-main"><div class="eyebrow">GMP 규제 용어집</div>'
            '<h1 class="cv-h1">모르는 용어,<br>여기서 찾으세요</h1>'
            '<p class="cv-sub">한·영 대조 · 쉬운 우리말 풀이 · 공식 출처</p><div class="cv-stats">'
            f'<div class="cv-stat"><b>{n_terms}</b><span>용어</span></div>'
            f'<div class="cv-stat"><b>{n_figs}</b><span>개념 그림</span></div>'
            f'<div class="cv-stat"><b>{n_cases}</b><span>실사 지적 사례가 있는 용어</span></div>'
            '</div></div>'
            f'<div class="cv-ft"><span>{_e(edition)} 판</span><b>grm-solutions.com/glossary</b></div></section>')


def intro() -> str:
    tiles = "".join(f'<a class="tile" href="{_e(_href(u))}"><b>{_e(h)}</b><p>{_e(p)}</p><span class="u">{_e(u)}</span></a>'
                    for h, p, u in TILES)
    return ('<section class="intro">'
            '<div class="more"><div class="tx"><b>더 자세한 내용이나 최신판이 필요하신가요?</b>'
            '<p>용어별 실제 실사 지적 사례와 최신판 용어집은 GRM 용어사전에서 볼 수 있습니다. 용어사전은 주기적으로 갱신됩니다.</p>'
            f'<a href="{_e(QR_GLOSSARY)}">grm-solutions.com/glossary →</a></div>'
            f'<div class="qr">{qr_svg(QR_GLOSSARY)}</div></div>'
            '<h2 class="sec">이 용어집은</h2><ul>'
            '<li>표제어를 한글과 영문으로 함께 적었습니다. 규정 원문에서 만나는 영어 표현을 그대로 찾을 수 있습니다.</li>'
            '<li>풀이는 쉬운 우리말로, 근거는 가이드라인·법령 조항 같은 공식 출처로 밝혔습니다.</li>'
            f'<li>용어 아래 <b>{_e(ONLINE.rstrip(" →"))}</b>를 누르면 GRM 용어사전으로 이동합니다. 그 용어가 실제 실사에서 지적된 문장까지 볼 수 있습니다.</li>'
            '</ul>'
            '<h2 class="sec">GRM은</h2>'
            '<p class="tag">모든 규제 신호를 단 <em>한곳으로</em>.</p>'
            '<p class="lead">FDA·EMA·MHRA·PIC/S·식약처의 GMP·품질 규제 소식을 공식 원문 링크와 함께 매주 한국어로 정리합니다.</p>'
            f'<div class="tiles">{tiles}</div>'
            '<p class="note">본 자료는 정보 제공용입니다. 최종 판단은 언제나 공식 원문과 최신 기준을 확인하세요.</p>'
            '</section>')


def index_pages(view: dict[str, Any]) -> str:
    parts = [f'<section class="idx"><h2 class="sec">전체 용어 {view["total"]}개</h2>']
    for g in view["groups"]:
        items = "".join(f'<li><a href="#{_e(t["id"])}">{_e(t["term"])}</a></li>' for t in g["terms"])
        parts.append(f'<div class="idx-g"><h3>{_e(g["bucket"])}</h3><ul>{items}</ul></div>')
    parts.append("</section>")
    return "".join(parts)


def entry(t: dict[str, Any]) -> str:
    fig = f'<div class="fig">{lc.mini(t["id"])}</div>' if t["id"] in lc.MINI_SVG else ""
    en = f'<span class="en">{_e(t["term_sub"])}</span>' if t.get("term_sub") else ""
    detail = f'<p class="detail">{_e(t["detail"])}</p>' if t.get("detail") else ""
    refs = " · ".join(r["label"] for r in (t.get("reg_refs") or []) if r.get("label"))
    meta = f'<b>출처</b> {_e(t["definition_source"])}'
    if refs:
        meta += f' &nbsp;·&nbsp; <b>관련 조항</b> {_e(refs)}'
    if t.get("related"):
        meta += ' &nbsp;·&nbsp; <b>함께 보기</b> ' + ", ".join(
            f'<a href="#{_e(r["id"])}">{_e(r["label"])}</a>' for r in t["related"])
    n = _case_count(t)
    online = f"실사 지적 사례 {n}건 · {ONLINE}" if n else ONLINE
    return (f'<article class="t" id="{_e(t["id"])}">{fig}<h4>{_e(t["term"])}{en}</h4>'
            f'<p class="easy">{_e(t["easy"])}</p>{detail}<p class="meta">{meta}</p>'
            f'<a class="online" href="{SITE}/glossary/{_e(t["id"])}/">{_e(online)}</a></article>')


def promo(i: int) -> str:
    h, p, u = PROMOS[i]
    return (f'<a class="promo" href="{_e(_href(u))}">{lc.OWL_SVG}<span><b>{_e(h)}</b><p>{_e(p)}</p></span>'
            f'<span class="u">{_e(u)} →</span></a>')


def promo_slots(n_groups: int) -> dict[int, int]:
    """초성 묶음 index → 광고 번호. 네 칸을 묶음 사이에 고르게 — 글자에 매지 않으니 묶음이
    늘거나 줄어도 광고가 조용히 사라지지 않는다(묶음이 넷보다 적으면 그만큼만)."""
    k = min(len(PROMOS), n_groups)
    return {((j + 1) * n_groups) // (k + 1): j for j in range(k)}


def body(view: dict[str, Any]) -> str:
    slots = promo_slots(len(view["groups"]))
    out = []
    for i, g in enumerate(view["groups"]):
        out.append(f'<section class="grp"><div class="grp-h">{_e(g["bucket"])}</div>')
        out.extend(entry(t) for t in g["terms"])
        if i in slots:
            out.append(promo(slots[i]))
        out.append("</section>")
    return "".join(out)


def back() -> str:
    return (f'<section class="full dark">{_brand(lc.OWL_SVG)}<div class="bk-main">'
            '<p class="bk-h">모든 규제 신호를<br>단 <em>한곳으로</em>.</p>'
            '<p class="bk-p">매주 월요일, 글로벌 GMP 규제 소식을 한국어로 받아보세요.</p>'
            '<ul class="bk-list"><li>주간 규제 브리프 · 뉴스레터</li><li>실사 지적사항 검색 · 규제 동향</li>'
            '<li>용어사전 · 주간 퀴즈</li></ul>'
            f'<div class="bk-qr"><div class="qr">{qr_svg(QR_HOME)}</div>'
            '<div class="tx"><b>최신판과 매주 규제 소식은 여기서</b>'
            f'<a href="{_e(QR_HOME)}">grm-solutions.com</a><br>휴대폰 카메라로 QR 을 비추면 바로 열립니다.</div></div></div>'
            '<div class="bk-ft">© 2026 Global Regulatory Monitor<br>'
            '본 서비스는 정보 제공 서비스이며, 제공 정보의 사용에 따른 최종 판단과 책임은 이용자에게 있습니다.</div></section>')


def edition_label(today: "datetime | None" = None) -> str:
    d = today or datetime.now(KST)
    return f"{d.year}년 {d.month}월"


def load_view() -> dict[str, Any]:
    terms = render.load_glossary()
    if not terms:
        raise SystemExit("glossary.json 을 읽지 못했다")
    return render.build_glossary_view(terms, cases=render.load_glossary_cases())


def build_html(view: dict[str, Any], edition: str, *, font_links: bool = True) -> str:
    all_terms = [t for g in view["groups"] for t in g["terms"]]
    n_figs = sum(1 for t in all_terms if t["id"] in lc.MINI_SVG)
    n_cases = sum(1 for t in all_terms if _case_count(t))
    css = (CSS.replace("HEX_DARK", lc._data_uri(lc._HEX.format(op="0.07")))
              .replace("HEX_CREAM", lc._data_uri(lc._HEX.format(op="0.07"))))
    return ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>GRM GMP 규제 용어집</title>'
            f'{lc.FONT_LINKS if font_links else ""}<style>{css}</style></head><body>'
            f'{cover(view["total"], n_figs, n_cases, edition)}{intro()}{index_pages(view)}{body(view)}{back()}'
            '</body></html>')


def verify_pdf(pdf: Path, n_terms: int) -> list[str]:
    """만든 PDF 를 다시 읽어 확인 — 용어마다 붙는 '온라인에서 보기'가 용어 수만큼 있어야 한다
    (페이지가 잘리거나 폰트가 빠져 글자가 사라지면 여기서 드러난다)."""
    import fitz
    problems: list[str] = []
    with fitz.open(pdf) as doc:
        text = "".join(p.get_text() for p in doc)
        if doc.page_count < 5:
            problems.append(f"쪽 수가 너무 적다: {doc.page_count}")
    if "GMP 규제 용어집" not in text:
        problems.append("표지 제목이 본문 텍스트에 없다")
    got = text.count(ONLINE)          # 화살표까지 — 소개 쪽의 안내 문장(화살표 없음)은 세지 않는다
    if got != n_terms:
        problems.append(f"'{ONLINE}' {got}개 ≠ 용어 {n_terms}개")
    return problems


def main(argv: "list[str] | None" = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="GMP 규제 용어집 PDF (구독자 전용 자료)")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "dist",
                    help="사이트 dist 루트. PDF 는 그 아래 render.GLOSSARY_PDF_PATH 에 쓴다")
    ap.add_argument("--keep-html", action="store_true", help="중간 HTML 을 남긴다(검토용)")
    args = ap.parse_args(argv)

    view = load_view()
    pdf = args.out / render.GLOSSARY_PDF_PATH
    pdf.parent.mkdir(parents=True, exist_ok=True)
    html_path = pdf.with_suffix(".html")
    html_path.write_text(build_html(view, edition_label()), encoding="utf-8")
    chrome = lc.find_chrome()
    if not chrome:
        print("::warning::Chrome 없음 — 용어집 PDF 를 만들지 못했다(HTML 만 남김)", file=sys.stderr)
        return 2
    try:
        lc.render_pdf(html_path, pdf, chrome, timeout=240)
    finally:
        if not args.keep_html:
            html_path.unlink(missing_ok=True)
    problems = verify_pdf(pdf, view["total"])
    if problems:
        print("::warning::용어집 PDF 확인 실패 — " + " / ".join(problems), file=sys.stderr)
        return 1
    print(f"용어집 PDF: 용어 {view['total']}개 · {pdf.stat().st_size:,} bytes → {pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
