#!/usr/bin/env python3
"""483 상세 번역 묶음 검사 — A·B·C 세 곳(이 세션·티로·Codex)이 같은 검사를 쓴다.

사용:  python translations/detail_shards/check.py translations/detail_outbox/A-0001.json [...]
각 outbox 파일을 같은 이름의 원본 묶음 파일(translations/detail_shards/queue/<묶음>/<이름>)과 맞대어
  ① 항목 수·finding_id·finding_text_md5·detail_text 가 원본과 **글자 그대로** 같다(영문을 고치지 않는다)
  ② detail_text_ko 가 있고 findings_detail_apply.validate_item 을 통과한다
  ③ findings_detail_qa.check 규칙(숫자·코드·[redacted]·한글·한자·길이)을 통과한다
를 본다. 하나라도 어기면 exit 1 — 고쳐서 다시 돌린다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import findings_detail_apply as app  # noqa: E402
import findings_detail_qa as qa  # noqa: E402

QUEUE = Path(__file__).resolve().parent / "queue"


def check_file(path: Path) -> list[str]:
    problems: list[str] = []
    shard = path.name.split("-", 1)[0]
    src_path = QUEUE / shard / path.name
    if not src_path.is_file():
        return [f"원본 묶음 파일 없음: {src_path.relative_to(ROOT)}"]
    src = json.loads(src_path.read_text(encoding="utf-8"))
    out = json.loads(path.read_text(encoding="utf-8"))
    if len(src) != len(out):
        problems.append(f"항목 수 {len(src)} → {len(out)}")
    for i, (s, o) in enumerate(zip(src, out)):
        for k in ("finding_id", "finding_text_md5", "detail_text"):
            if s.get(k) != o.get(k):
                problems.append(f"#{i} {s.get('finding_id')}: {k} 가 원본과 다름")
        why = app.validate_item(o)
        if why:
            problems.append(f"#{i} {o.get('finding_id')}: {why}")
            continue
        for p in qa.check(o["detail_text"], o["detail_text_ko"]):
            problems.append(f"#{i} {o['finding_id']}: {p}")
    return problems


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    bad = 0
    for a in argv:
        probs = check_file(Path(a))
        print(f"{Path(a).name}: {'OK' if not probs else f'{len(probs)}건 문제'}")
        for p in probs[:40]:
            print(f"  {p}")
        bad += len(probs)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
