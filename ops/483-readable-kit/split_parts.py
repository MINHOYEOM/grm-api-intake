"""대기열 파일을 번역자용 조각(finding_id·detail_text 만)으로 나눈다."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
src, per = sys.argv[1], int(sys.argv[2])
items = json.loads((HERE / "queue" / f"{src}.json").read_text(encoding="utf-8"))
parts = HERE / "parts"
parts.mkdir(exist_ok=True)
for k in range(0, len(items), per):
    chunk = [{"finding_id": x["finding_id"], "detail_text": x["detail_text"]} for x in items[k:k + per]]
    name = f"{src}-p{k // per + 1}"
    (parts / f"{name}.in.json").write_text(json.dumps(chunk, ensure_ascii=False, indent=1), encoding="utf-8")
    print(name, len(chunk))
