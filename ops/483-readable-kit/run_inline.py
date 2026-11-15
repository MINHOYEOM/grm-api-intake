"""셸 없이 Codex 번역: 입력을 지시문에 넣고 마지막 메시지(JSON)를 받는다.

사용: python run_inline.py X-0005-p1            (전체)
      python run_inline.py X-0005-p1 --fix      (check_part 실패 항목만 다시, 결과를 out.json 에 덮어 합침)
회사PC Codex 샌드박스는 셸 실행이 막혀(apply deny-read ACLs / CreateProcessAsUserW 5) 도구 없이 답만 받는다.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).parent
import glob, os
C = os.environ.get("CODEX_EXE") or (sorted(glob.glob(r"C:\Program Files\WindowsApps\OpenAI.Codex_*\app\resources\codex.exe")) or ["codex"])[-1]
part = sys.argv[1]
fix = "--fix" in sys.argv
items = json.loads((HERE / "parts" / f"{part}.in.json").read_text(encoding="utf-8"))
out_path = HERE / "parts" / f"{part}.out.json"
fails = {}
redo = next((a.split("=",1)[1] for a in sys.argv if a.startswith("--redo=")), "")
if redo:
    fix = True
    prev_all = json.loads(out_path.read_text(encoding="utf-8"))
    fails = {}
    for fid in redo.split(","):
        v = prev_all.get(fid); kt = v.get("kept_tokens") if isinstance(v, dict) else None
        fails[fid] = ("kept_tokens 남용 의심: " + ", ".join(kt or []) + " — 문서·배치·장비 번호 같은 식별자는 원문 그대로 두되, "
                      "단위·낱말·날짜·법 조항·약어의 OCR 오독(l0mL→10mL, Sa1rncenia→Sarracenia, 2O24→2024, LOOQ→LOQ, (I0)→(10))은 바로잡고 "
                      "그 원래 토큰은 ocr_dropped 에 적는다. kept_tokens 에는 진짜 이름만 남긴다.")
    items = [it for it in items if it["finding_id"] in fails]
elif fix:
    r = subprocess.run([sys.executable, str(HERE / "check_part.py"), part, "--json"], capture_output=True, text=True, encoding="utf-8")
    fails = json.loads(r.stdout.strip().splitlines()[-1])
    items = [it for it in items if it["finding_id"] in fails]
    if not items:
        print("nothing to fix"); sys.exit(0)

brief = (HERE / "BRIEF.md").read_text(encoding="utf-8")
rules = (
    "도구·셸 명령을 쓰지 마라. 어떤 파일도 읽거나 쓰지 마라. 아래 지침과 입력만으로 답한다.\n"
    "찌꺼기처럼 보이는 실제 이름(Anti-C3d·Pro1x·Mix2Vial)은 바꾸지 말고 kept_tokens 로 적는다. "
    "중복으로 덜 쓴 가림 표시는 ocr_dropped 에 \"[redacted]\" 로 적을 수 있다. 숫자·연도를 추측해 고치지 마라"
    "(고쳤다면 \"(원문 표기 …)\" 병기). kept_tokens 는 진짜 제품·장비·문서 이름만이다. 뜻이 분명한 OCR 오독(Bl2→B12, Methylcobalarnin→Methylcobalamin)은 바로잡는다. 검사를 통과하려고 국문에 찌꺼기·머리글·설명 문장을 덧붙이지 마라.\n"
    "답은 JSON 객체 하나만 낸다(코드 블록·설명 없이): {finding_id: \"국문\"} 또는 "
    "{finding_id: {\"ko\": \"국문\", \"ocr_dropped\": [...], \"kept_tokens\": [...]}}. 입력 항목 전부를 빠짐없이.\n"
)
body = "# 지침(BRIEF.md)\n" + brief + "\n\n# 추가 규칙\n" + rules
if fix:
    body += "\n# 이전 번역이 검사에서 걸린 이유(고칠 것)\n" + json.dumps(fails, ensure_ascii=False, indent=1) + "\n"
    if out_path.exists():
        prev = json.loads(out_path.read_text(encoding="utf-8"))
        body += "\n# 이전 번역\n" + json.dumps({k: prev.get(k) for k in fails}, ensure_ascii=False) + "\n"
body += "\n# 입력\n" + json.dumps(items, ensure_ascii=False)

logs = HERE / "logs"
logs.mkdir(exist_ok=True)
tag = part + (".redo" if redo else ".fix" if fix else "")
(logs / f"{tag}.prompt.txt").write_text(body, encoding="utf-8")
last = logs / f"{tag}.last.txt"
with open(logs / f"{tag}.log", "w", encoding="utf-8") as lf:
    p = subprocess.run([C, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
                        "-C", str(HERE / "parts"), "-o", str(last), "-"],
                       input=body.encode("utf-8"), stdout=lf, stderr=subprocess.STDOUT)
txt = last.read_text(encoding="utf-8") if last.exists() else ""
m = re.search(r"\{.*\}", txt, re.S)
if not m:
    print("NO_JSON exit", p.returncode, txt[:300]); sys.exit(1)
got = json.loads(m.group(0))
merged = json.loads(out_path.read_text(encoding="utf-8")) if (fix and out_path.exists()) else {}
merged.update(got)
out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
missing = [it["finding_id"] for it in items if it["finding_id"] not in got]
print("got", len(got), "of", len(items), "missing", len(missing))
