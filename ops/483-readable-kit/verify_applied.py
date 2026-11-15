"""적용 확인: 사용법 python verify_applied.py X-0003 X-0004 — 배치 파일은 worktree 의 origin/main 기준."""
import hashlib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
WT = str(Path(__file__).resolve().parents[2])  # repo root
sys.path.insert(0, WT)
import findings_detail_qa as qa  # noqa: E402

HERE = Path(__file__).parent
snap = {}
for qf in sorted((HERE / "queue").glob("X-*.json")):
    snap.update({r["finding_id"]: r for r in json.loads(qf.read_text(encoding="utf-8"))})
ids = []
for n in sys.argv[1:]:
    raw = subprocess.run(["git", "-C", WT, "show", f"origin/main:translations/detail_outbox/{n}.json"],
                         capture_output=True, check=True).stdout.decode("utf-8")
    ids += [x["finding_id"] for x in json.loads(raw)]
KEY = "sb_publishable_beMJop3vtmwg9hkQNlSFRg_Jh15NrK9"
live = {}
for i in range(0, len(ids), 80):
    q = ("https://rfwixqqdljpmtjdlblct.supabase.co/rest/v1/findings?select=finding_id,detail_text,detail_text_ko"
         "&finding_id=in.(" + ",".join(ids[i:i + 80]) + ")")
    req = urllib.request.Request(q, headers={"apikey": KEY, "Authorization": "Bearer " + KEY})
    for r in json.loads(urllib.request.urlopen(req, timeout=60).read()):
        live[r["finding_id"]] = r


def md5(t):
    return hashlib.md5(t.encode("utf-8")).hexdigest()


diff = [f for f in ids if md5(live[f]["detail_text"]) != md5(snap[f]["detail_text"])]
bad = [f for f in ids if qa.readability(live[f]["detail_text_ko"])]
print("rows", len(ids), "live", len(live), "en_md5_changed", len(diff), "still_unreadable", len(bad))
