"""parts/<X>-p*.out.json 을 합쳐 detail_outbox 배치 + 전후 대조 보고를 만든다.

사용: python assemble.py X-0001 <worktree>
- 배치 항목 = 대기열의 finding_id·finding_text_md5·detail_text(운영 DB 값 그대로) + 새 detail_text_ko
- 대조(옛 국문 → 새 국문): 새 국문은 strict 통과, 옛 국문에 있던 숫자 묶음이 새 국문에서 사라지면(머리글 영역 제외)
  'regress', 새/옛 길이 비 < 0.55 면 'shrunk' 로 표시하고 그 항목은 배치에서 뺀다.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
name, wt = sys.argv[1], Path(sys.argv[2])
accept = set(sys.argv[3].split(",")) if len(sys.argv) > 3 else set()  # 사람이 읽고 통과시킨 shrunk 항목
sys.path.insert(0, str(wt))
import findings_detail_qa as qa  # noqa: E402

HERE = Path(__file__).parent
queue = json.loads((HERE / "queue" / f"{name}.json").read_text(encoding="utf-8"))
ko = {}
for p in sorted((HERE / "parts").glob(f"{name}-p*.out.json")):
    ko.update(json.loads(p.read_text(encoding="utf-8")))

NUM = re.compile(r"(?<![A-Za-z])\d+(?:[.,/:\-]\d+)*(?![A-Za-z])")
out, report, held = [], [], []
old_bad = new_bad = 0
for it in queue:
    fid = it["finding_id"]
    v = ko.get(fid)
    new, dropped, kept = (v.get("ko"), v.get("ocr_dropped"), v.get("kept_tokens")) if isinstance(v, dict) else (v, None, None)
    old = it["old_detail_text_ko"]
    if not new:
        held.append((fid, "no_translation"))
        continue
    probs = qa.check(it["detail_text"], new, strict=True, ocr_dropped=dropped, kept_tokens=kept)
    mask = qa._header_mask(it["detail_text"])
    en_nums = Counter(m.group(0).rstrip(".,") for m in NUM.finditer(it["detail_text"]) if qa._outside(mask, m.start()))
    lost = [n for n in Counter(m.group(0).rstrip(".,") for m in NUM.finditer(old))
            if n in en_nums and n not in new and n not in (dropped or [])]
    ratio = len(new) / max(1, len(old))
    flags = list(probs)
    if lost:
        flags.append("regress_numbers:" + ",".join(lost[:5]))
    if ratio < 0.55 and fid not in accept:
        flags.append(f"shrunk:{ratio:.2f}")
    old_r = qa.readability(old)
    old_bad += bool(old_r)
    new_bad += bool(qa.readability(new, kept))
    report.append({"finding_id": fid, "document_id": it["document_id"], "old_problems": old_r,
                   "flags": flags, "ocr_dropped": dropped, "ratio": round(ratio, 2), "old": old, "new": new, "en": it["detail_text"]})
    if flags:
        held.append((fid, "; ".join(flags)))
        continue
    row = {"finding_id": fid, "finding_text_md5": it["finding_text_md5"],
           "detail_text": it["detail_text"], "detail_text_ko": new}
    if dropped:
        row["ocr_dropped"] = dropped
    if kept:
        row["kept_tokens"] = kept
    out.append(row)

dst = wt / "translations" / "detail_outbox" / f"{name}.json"
dst.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
(HERE / f"report-{name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"{name}: queue {len(queue)} → batch {len(out)} · held {len(held)} · readability problems old {old_bad} → new {new_bad}")
for fid, why in held:
    print("  HELD", fid, why)
