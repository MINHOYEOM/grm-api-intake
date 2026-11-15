"""번역자 출력({finding_id: ko}) 을 원문과 맞대어 strict 검사. 사용: python check_part.py <part이름>"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root (findings_detail_qa)
import findings_detail_qa as qa  # noqa: E402

HERE = Path(__file__).parent
name = sys.argv[1]
src = json.loads((HERE / "parts" / f"{name}.in.json").read_text(encoding="utf-8"))
out = json.loads((HERE / "parts" / f"{name}.out.json").read_text(encoding="utf-8"))
bad = 0
AS_JSON = "--json" in sys.argv
fails = {}
for it in src:
    v = out.get(it["finding_id"])
    ko, dropped, kept = (v.get("ko"), v.get("ocr_dropped"), v.get("kept_tokens")) if isinstance(v, dict) else (v, None, None)
    if not ko:
        fails[it["finding_id"]] = "MISSING"
        if not AS_JSON: print(it["finding_id"], "MISSING")
        bad += 1
        continue
    probs = qa.check(it["detail_text"], ko, strict=True, ocr_dropped=dropped, kept_tokens=kept)
    if probs:
        bad += 1
        fails[it["finding_id"]] = "; ".join(probs)
        if not AS_JSON: print(it["finding_id"], "; ".join(probs))
extra = set(out) - {it["finding_id"] for it in src}
if extra:
    print("EXTRA ids", sorted(extra))
    bad += 1
print(json.dumps(fails, ensure_ascii=False) if AS_JSON else ("OK" if not bad else f"{bad} problems"))
