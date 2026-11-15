"""PR 전: 배치의 detail_text 가 운영 DB 와 같은지 + REST 응답 시간. 사용: python precheck.py X-0005"""
import hashlib, json, sys, time, urllib.request
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
WT = str(Path(__file__).resolve().parents[2])  # repo root
rows = json.load(open(f"{WT}/translations/detail_outbox/{sys.argv[1]}.json", encoding="utf-8"))
KEY = "sb_publishable_beMJop3vtmwg9hkQNlSFRg_Jh15NrK9"
ids = [r["finding_id"] for r in rows]; live = {}; worst = 0
for i in range(0, len(ids), 80):
    q = ("https://rfwixqqdljpmtjdlblct.supabase.co/rest/v1/findings?select=finding_id,detail_text&finding_id=in.(" + ",".join(ids[i:i+80]) + ")")
    t = time.time()
    for r in json.loads(urllib.request.urlopen(urllib.request.Request(q, headers={"apikey": KEY, "Authorization": "Bearer " + KEY}), timeout=30).read()):
        live[r["finding_id"]] = r["detail_text"]
    worst = max(worst, time.time() - t)
md5 = lambda s: hashlib.md5((s or "").encode()).hexdigest()
diff = [r["finding_id"] for r in rows if md5(r["detail_text"]) != md5(live.get(r["finding_id"]))]
print("rows", len(rows), "live", len(live), "en_differs", len(diff), f"rest_max {worst:.2f}s")
t = time.time()
urllib.request.urlopen(urllib.request.Request("https://rfwixqqdljpmtjdlblct.supabase.co/rest/v1/findings?select=finding_id&limit=1", headers={"apikey": KEY, "Authorization": "Bearer " + KEY}), timeout=30).read()
probe = time.time() - t
print(f"probe {probe:.2f}s")
if diff or probe >= 2.0 or len(live) != len(rows):
    print("STOP: 원문 불일치 또는 REST 2초 이상 — 반영하지 말 것"); sys.exit(1)
