#!/usr/bin/env python3
"""용어 그림 일일 배치의 도구 — 후보 순서(queue) · 글자 실측(check) · 검수 시트(sheet).

그림을 **그릴지, 어떻게 그릴지**는 세션이 정의를 읽고 정한다. 이 스크립트는 그 앞뒤를 기계로
굳힌다: 무엇을 먼저 볼지, 그린 것이 viewBox 를 넘거나 글자끼리 부딪히지 않는지, 정의와 나란히
놓고 볼 시트. 규율 문서: docs/prompts/GRM_용어그림_일일배치_프롬프트_v1.md

  python glossary_figs.py queue --top 6            # 다시 볼 그림(redo) → 새 후보(카드 노출 순)
  python glossary_figs.py check hepa-filter oos    # 두 언어 × Pretendard 실측 — 문제 있으면 exit 1
  python glossary_figs.py check --all
  python glossary_figs.py sheet hepa-filter oos --out sheet.png

그림은 한 벌뿐이다(`web/partials/glossary_fig/<id>.html`). 카드(linkedin_cards.mini)는 그
partial 을 사이트와 같은 번역기로 렌더해 쓰므로, 여기서 재는 것이 곧 사이트·카드 둘 다다.
장부 `web/data/glossary_fig_review.json` 은 **다시 볼 그림(redo)** 과 **그리지 않기로 한 용어
(skip)** 를 사유와 함께 적는다 — 매일 같은 용어를 다시 판정하지 않기 위해서다.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
WEB_DIR = REPO_ROOT / "web"
sys.path.insert(0, str(WEB_DIR))

import linkedin_cards as lc  # noqa: E402

GLOSSARY = WEB_DIR / "data" / "glossary.json"
REVIEW = WEB_DIR / "data" / "glossary_fig_review.json"
BRIEFS = WEB_DIR / "data" / "briefs"


def load_glossary() -> list[dict]:
    return json.loads(GLOSSARY.read_text(encoding="utf-8"))


def load_review() -> dict:
    if not REVIEW.exists():
        return {"redo": {}, "skip": {}}
    data = json.loads(REVIEW.read_text(encoding="utf-8"))
    return {"redo": data.get("redo") or {}, "skip": data.get("skip") or {}}


# ── queue ─────────────────────────────────────────────────────────────────────
def card_exposure(glossary: list[dict]) -> dict[str, dict]:
    """발행 브리프마다 카드 덱과 **같은 규칙**(lc.pick_glossary_terms)으로 용어 점수를 낸다.
    top5 = 실제로 '이번 주 용어' 장에 실렸을 주 수, scored = 점수가 난 주 수. 그림이 있으면 먼저
    보일 용어부터 그리려는 것이다(카드는 매주 나가고, 빈 칸은 거기서 보인다)."""
    out: dict[str, dict] = {}
    for doc in lc.load_all_briefs(BRIEFS):
        brief = doc.get("brief") or {}
        cards = sorted(doc.get("cards") or [], key=lambda c: int(c.get("render_order") or 0))
        heads = lc.pick_headline_cards(brief, cards, 3)
        ranked = lc.pick_glossary_terms(glossary, cards, len(glossary), headline_cards=heads)
        for rank, t in enumerate(ranked):
            e = out.setdefault(t["id"], {"top5": 0, "scored": 0, "best": 10**6})
            e["scored"] += 1
            e["top5"] += rank < 5
            e["best"] = min(e["best"], rank + 1)
    return out


def build_queue(top: int) -> dict:
    glossary = load_glossary()
    review = load_review()
    figs = set(lc.figure_ids())
    by_id = {t["id"]: t for t in glossary}
    exp = card_exposure(glossary)
    order = {t["id"]: i for i, t in enumerate(glossary)}

    def row(tid: str) -> dict:
        t = by_id.get(tid, {})
        e = exp.get(tid, {"top5": 0, "scored": 0, "best": None})
        return {"id": tid, "term_ko": t.get("term_ko"), "term_en": t.get("term_en"),
                "easy_ko": t.get("easy_ko"), "easy_en": t.get("easy_en"),
                "definition_source": t.get("definition_source"),
                "related": [{"id": r, "has_figure": r in figs} for r in (t.get("related") or [])],
                "card_weeks_top5": e["top5"], "card_weeks_scored": e["scored"]}

    redo = [dict(row(tid), reason=v.get("reason"), since=v.get("since"))
            for tid, v in sorted(review["redo"].items()) if tid in figs]
    # 다시 그릴 그림도 **카드에 자주 실린 것부터** — 이름순이면 15주 중 8주 실린 무균조작이 한 번도 안 실린
    # 적합기준 뒤로 밀린다(2026-09-29 실측). 매주 나가는 칸의 결함을 먼저 고친다.
    redo.sort(key=lambda r: (-r["card_weeks_top5"], -r["card_weeks_scored"], r["id"]))
    todo = [t["id"] for t in glossary if t["id"] not in figs and t["id"] not in review["skip"]]
    todo.sort(key=lambda tid: (-exp.get(tid, {}).get("top5", 0), -exp.get(tid, {}).get("scored", 0),
                               exp.get(tid, {}).get("best") or 10**6, order[tid]))
    return {"counts": {"terms": len(glossary), "figures": len(figs), "skip": len(review["skip"]),
                       "redo": len(redo), "remaining": len(todo)},
            "redo": redo, "new": [row(tid) for tid in todo[:top]]}


# ── check · sheet 공용: 헤드리스 Chrome + 로컬 HTTP(Pretendard 는 CDN) ─────────────────────
class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # 요청 로그가 측정 결과를 덮지 않게
        pass


def _serve(root: Path) -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _chrome(args: list[str], timeout: int = 120) -> str:
    chrome = lc.find_chrome()
    if not chrome:
        raise SystemExit("Chrome 을 찾지 못했다 — GRM_CHROME 환경변수로 경로를 준다")
    profile = Path(tempfile.mkdtemp(prefix="grm-fig-chrome-"))
    try:
        r = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                            "--no-first-run", "--no-default-browser-check", f"--user-data-dir={profile}",
                            "--virtual-time-budget=12000", *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return r.stdout
    finally:
        shutil.rmtree(profile, ignore_errors=True)


PAGE_HEAD = ('<!doctype html><html><head><meta charset="utf-8">' + lc.FONT_LINKS +
             "<style>body{margin:0;padding:12px;background:#FAF9F5;font-family:'Pretendard',sans-serif}"
             ".mini text{font-family:'Pretendard',sans-serif}</style></head><body>")

# 규칙(모두 SVG 사용자 단위, 즉 viewBox 좌표):
#  R1 글자가 viewBox 밖으로 나감            R2 글자끼리 겹침(1 단위 여유)
#  R3 글자가 상자(rect) 경계에 걸침         R4 상자 안 글자는 좌우 여백 2 이상
#  R5 글자가 점(circle)에 붙거나 덮음       R6 글자가 작은 표식(16 이하 rect)에 붙거나 덮음
#     (R5·R6 은 두 상자 사이 **실제 거리** < 2 — 대각선으로 떨어진 것은 붙은 게 아니다)
#  F  Pretendard 가 실제로 로드되지 않았으면 판정 무효
# ★모든 상자는 **viewBox 좌표로 환산**해 잰다(회전·부모 g 의 transform 포함). getBBox 는 자기 transform 을
#   빼고 돌려주므로, 회전한 막대 표식은 그대로 쓰면 엉뚱한 자리에 있는 것으로 잰다.
# ★R6(2026-09-29): 무균조작 다시 그림에서 영문 라벨 "Microbes, endotoxin, particles" 가 오른쪽 위 막대 표식을
#   덮었는데 R1~R5 가 통과시켰다 — 점(circle)만 보고 작은 막대(rect)는 안 봤기 때문이다. 국문은 짧아 멀쩡했다.
MEASURE_JS = r"""
(async () => {
  await document.fonts.ready;
  const out = {fonts: document.fonts.check("700 10px Pretendard") && document.fonts.check("400 10px Pretendard"), items: []};
  const inter = (a, b, p = 0) => a.x < b.x + b.width + p && b.x < a.x + a.width + p && a.y < b.y + b.height + p && b.y < a.y + a.height + p;
  // 두 상자 사이의 실제 거리(겹치면 0) — 가로·세로 여유를 따로 보면 대각선으로 떨어진 것까지 붙었다고 잰다
  const gap = (a, b) => Math.hypot(Math.max(0, b.x - (a.x + a.width), a.x - (b.x + b.width)),
                                   Math.max(0, b.y - (a.y + a.height), a.y - (b.y + b.height)));
  const inside = (a, b) => a.x >= b.x && a.y >= b.y && a.x + a.width <= b.x + b.width && a.y + a.height <= b.y + b.height;
  const r1 = n => Math.round(n * 10) / 10;
  for (const box of document.querySelectorAll(".box")) {
    const svg = box.querySelector("svg"), vb = svg.viewBox.baseVal, probs = [];
    const toVb = svg.getScreenCTM().inverse();
    const bb = e => {           // 요소의 상자를 viewBox 좌표로(자기·조상 transform 포함)
      const b = e.getBBox(), m = toVb.multiply(e.getScreenCTM());
      const pts = [[b.x, b.y], [b.x + b.width, b.y], [b.x, b.y + b.height], [b.x + b.width, b.y + b.height]]
        .map(([x, y]) => [m.a * x + m.c * y + m.e, m.b * x + m.d * y + m.f]);
      const xs = pts.map(q => q[0]), ys = pts.map(q => q[1]);
      return {x: Math.min(...xs), y: Math.min(...ys), width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys)};
    };
    const T = [...svg.querySelectorAll("text")].map(t => ({s: t.textContent.trim(), b: bb(t)})).filter(t => t.s);
    const R = [...svg.querySelectorAll("rect")].map(bb).filter(b => !(b.width >= vb.width - 1 && b.height >= vb.height - 1));
    // 점은 네모가 아니라 **원**으로 잰다 — 네모 상자의 모서리는 원보다 훨씬 바깥이라 거짓으로 붙었다고 나온다
    const C = [...svg.querySelectorAll("circle")].map(e => {
      const m = toVb.multiply(e.getScreenCTM()), x = e.cx.baseVal.value, y = e.cy.baseVal.value;
      return {cx: m.a * x + m.c * y + m.e, cy: m.b * x + m.d * y + m.f,
              r: e.r.baseVal.value * Math.sqrt(Math.abs(m.a * m.d - m.b * m.c))};
    });
    const gapCircle = (a, c) => Math.max(0, Math.hypot(Math.max(0, a.x - c.cx, c.cx - (a.x + a.width)),
                                                       Math.max(0, a.y - c.cy, c.cy - (a.y + a.height))) - c.r);
    for (const t of T) {
      const b = t.b;
      if (b.x < vb.x - 0.5 || b.y < vb.y - 0.5 || b.x + b.width > vb.x + vb.width + 0.5 || b.y + b.height > vb.y + vb.height + 0.5)
        probs.push(`R1 viewBox 밖: "${t.s}" (${r1(b.x)}..${r1(b.x + b.width)}, ${r1(b.y)}..${r1(b.y + b.height)} / ${vb.width}x${vb.height})`);
      for (const rb of R) {
        const cx = b.x + b.width / 2, cy = b.y + b.height / 2;
        const centred = cx > rb.x && cx < rb.x + rb.width && cy > rb.y && cy < rb.y + rb.height;
        if (rb.width <= 16 && rb.height <= 16 && !centred) {   // 작은 표식 — 2 단위 안으로 붙어도 글자의 일부로 읽힌다
          if (gap(b, rb) < 2) probs.push(`R6 작은 표식에 붙음/덮음: "${t.s}" (${r1(rb.x)},${r1(rb.y)})`);
          continue;
        }
        if (!inter(b, rb)) continue;
        if (!inside(b, rb)) { if (centred) probs.push(`R3 상자 경계에 걸침: "${t.s}"`); }
        else if (b.x - rb.x < 2 || rb.x + rb.width - (b.x + b.width) < 2) probs.push(`R4 상자 안 여백 2 미만: "${t.s}"`);
      }
      for (const c of C) if (gapCircle(b, c) < 2) probs.push(`R5 점에 붙음/덮음: "${t.s}" (${r1(c.cx)},${r1(c.cy)})`);
    }
    for (let i = 0; i < T.length; i++) for (let j = i + 1; j < T.length; j++)
      if (inter(T[i].b, T[j].b, 1)) probs.push(`R2 글자 겹침: "${T[i].s}" ↔ "${T[j].s}"`);
    out.items.push({key: box.dataset.key, problems: [...new Set(probs)]});
  }
  const pre = document.createElement("pre"); pre.id = "result"; pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
})();
"""


def check(ids: list[str]) -> int:
    missing = [i for i in ids if not lc.has_figure(i)]
    if missing:
        raise SystemExit(f"그림이 없는 id: {missing}")
    boxes = "".join(f'<div class="box" data-key="{i}|{lang}">{lc.mini(i, lang)}</div>'
                    for i in ids for lang in lc.LANGS)
    tmp = Path(tempfile.mkdtemp(prefix="grm-fig-check-"))
    (tmp / "check.html").write_text(PAGE_HEAD + boxes + f"<script>{MEASURE_JS}</script></body></html>",
                                    encoding="utf-8")
    srv, base = _serve(tmp)
    try:
        dom = _chrome(["--dump-dom", f"{base}/check.html"])
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    m = re.search(r'<pre id="result">(.*?)</pre>', dom, re.S)
    if not m:
        print("측정 실패 — Chrome 이 결과를 내지 않았다(네트워크·Chrome 경로 확인)")
        return 2
    res = json.loads(html.unescape(m.group(1)))
    if not res["fonts"]:
        print("측정 무효 — Pretendard 가 로드되지 않았다(CDN 접속 확인). 폴백 글꼴로 잰 값은 믿지 않는다.")
        return 2
    bad = [it for it in res["items"] if it["problems"]]
    for it in res["items"]:
        print(f'{it["key"]:<40} {"OK" if not it["problems"] else " | ".join(it["problems"])}')
    print(f"\n{len(ids)}개 × {len(lc.LANGS)}언어 — 문제 {len(bad)}건")
    return 1 if bad else 0


# ── sheet ─────────────────────────────────────────────────────────────────────
def sheet(ids: list[str], out: Path, *, blank_names: bool = False) -> None:
    """정의와 그림을 나란히 — 한 줄에 용어 하나(국문 그림 · 영문 그림 · 두 정의).
    blank_names=True 면 용어 이름을 가린다(그림만 보고 정의를 맞히는 눈가림 검토용)."""
    by_id = {t["id"]: t for t in load_glossary()}
    rows = []
    for n, i in enumerate(ids, 1):
        t = by_id.get(i, {})
        name = f"#{n}" if blank_names else f'#{n} {html.escape(t.get("term_ko") or "")} · {html.escape(t.get("term_en") or "")} <small>{i}</small>'
        rows.append(f'<div class="r"><div class="n">{name}</div>'
                    f'<div class="f">{lc.mini(i, "ko")}</div><div class="f">{lc.mini(i, "en")}</div>'
                    f'<div class="d"><p>{html.escape(t.get("easy_ko") or "")}</p>'
                    f'<p class="e">{html.escape(t.get("easy_en") or "")}</p></div></div>')
    css = ("<style>.r{display:grid;grid-template-columns:150px 250px 250px 1fr;gap:12px;align-items:center;"
           "background:#fff;border:1px solid #ddd;padding:8px;margin-bottom:8px}"
           ".n{font-weight:700;font-size:14px}.n small{display:block;font-weight:400;color:#999;font-size:11px}"
           ".f svg{width:250px;height:auto;display:block}.d{font-size:12.5px;line-height:1.45;color:#333}"
           ".d p{margin:0 0 4px}.d .e{color:#666}</style>")
    tmp = Path(tempfile.mkdtemp(prefix="grm-fig-sheet-"))
    (tmp / "sheet.html").write_text(PAGE_HEAD + css + "".join(rows) + "</body></html>", encoding="utf-8")
    srv, base = _serve(tmp)
    try:
        height = 40 + 185 * len(ids)   # 줄 높이는 정의 길이에 따라 150~180 — 모자라면 마지막 줄이 잘린다
        _chrome([f"--window-size=1200,{height}", f"--screenshot={out.resolve()}", f"{base}/sheet.html"])
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    if not out.exists():
        raise SystemExit(f"시트 생성 실패: {out}")
    print(out)


def main(argv: list[str] | None = None) -> int:
    # Windows 콘솔(cp949)에서 한글·em-dash 출력으로 죽지 않게 — 배치는 PowerShell 에서 돈다
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="용어 그림 일일 배치 도구")
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("queue", help="다시 볼 그림 → 새 후보(카드 노출 순)")
    q.add_argument("--top", type=int, default=6)
    q.add_argument("--json", action="store_true")
    c = sub.add_parser("check", help="두 언어 × Pretendard 실측(viewBox·겹침·상자)")
    c.add_argument("ids", nargs="*")
    c.add_argument("--all", action="store_true")
    s = sub.add_parser("sheet", help="정의와 나란히 놓은 검수 시트 PNG")
    s.add_argument("ids", nargs="*")
    s.add_argument("--all", action="store_true")
    s.add_argument("--out", required=True)
    s.add_argument("--blank-names", action="store_true", help="용어 이름을 가린다(눈가림 검토)")
    a = ap.parse_args(argv)

    if a.cmd == "queue":
        qd = build_queue(a.top)
        if a.json:
            print(json.dumps(qd, ensure_ascii=False, indent=1))
            return 0
        c_ = qd["counts"]
        print(f'용어 {c_["terms"]} · 그림 {c_["figures"]} · 그리지 않기로 함 {c_["skip"]} · 다시 볼 그림 {c_["redo"]} · 남은 후보 {c_["remaining"]}')
        for r in qd["redo"]:
            print(f'\n[다시 보기] {r["id"]} ({r["term_ko"]}) — {r["reason"]}\n  정의: {r["easy_ko"]}')
        for r in qd["new"]:
            rel = ", ".join(f'{x["id"]}{"*" if x["has_figure"] else ""}' for x in r["related"])
            print(f'\n[새 후보] {r["id"]} ({r["term_ko"]} · {r["term_en"]}) — 카드 실림 {r["card_weeks_top5"]}주 · 점수 {r["card_weeks_scored"]}주'
                  f'\n  정의: {r["easy_ko"]}\n  출처: {r["definition_source"]}\n  관련(*=그림 있음): {rel}')
        return 0
    ids = lc.figure_ids() if a.all else a.ids
    if not ids:
        ap.error("id 를 주거나 --all")
    if a.cmd == "check":
        return check(ids)
    sheet(ids, Path(a.out), blank_names=a.blank_names)
    return 0


if __name__ == "__main__":
    sys.exit(main())
