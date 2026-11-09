"""import_alerts_refresh.py — 공개 스냅샷(화이트리스트·정렬·게이트) 단위 테스트(네트워크 없음)."""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import import_alerts_refresh as r  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "import_alerts" / "import_alerts_small.json"


def _row(**kw):
    base = {"alert_no": "66-40", "firm_key": "66-40|KOREA THE REPUBLIC OF|ALPHAPHARMA",
            "firm_name": "Alpha Pharma Co., Ltd.", "name_norm": "ALPHA PHARMA",
            "country": "KOREA (THE REPUBLIC OF)", "city": "Cheonan",
            "firm_published": "2024-04-10", "product_codes": ["66VBY07", "53LD99"],
            "active": True, "first_seen": "2026-11-09",
            # 원장에 있는 비공개 열 — 스냅샷에 실리면 안 된다.
            "address": "123 Secret-ro, Cheonan", "addresses": ["123 Secret-ro"],
            "products": [{"code": "53LD99", "description": "x"}], "delisted_on": None}
    base.update(kw)
    return base


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class _Http:
    def __init__(self, pages, status=200):
        self.pages, self.status, self.calls = list(pages), status, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return _Resp(self.status, self.pages.pop(0) if self.pages else [])


class BuildSnapshotTest(unittest.TestCase):
    def test_whitelist_only_no_address(self):
        snap = r.build_snapshot([_row()], measured_on="2026-11-10")
        row = snap["listings"][0]
        self.assertEqual(set(row), set(r.FIELDS))
        text = r.serialize(snap)
        for leaked in ("Secret-ro", "address", "description", "delisted_on"):
            self.assertNotIn(leaked, text)

    def test_sort_totals_and_codes(self):
        rows = [_row(alert_no="66-41", firm_key="66-41|INDIA|Z", country="INDIA"),
                _row(),
                _row(firm_key="66-40|JAPAN|B", country="JAPAN", active=False)]
        snap = r.build_snapshot(rows, measured_on="2026-11-10")
        self.assertEqual([x["firm_key"] for x in snap["listings"]],
                         ["66-40|JAPAN|B", "66-40|KOREA THE REPUBLIC OF|ALPHAPHARMA",
                          "66-41|INDIA|Z"])
        self.assertEqual(snap["totals"]["active"], 2)
        self.assertEqual(snap["totals"]["active_korea"], 1)
        self.assertEqual(snap["listings"][1]["product_codes"], ["53LD99", "66VBY07"])

    def test_rejects_empty_and_bad_rows(self):
        with self.assertRaises(r.SnapshotError):
            r.build_snapshot([], measured_on="2026-11-10")
        for bad in (_row(alert_no="6640"), _row(firm_name=""), _row(active="yes"),
                    _row(first_seen="unknown"), _row(firm_published="10/01/2024")):
            with self.assertRaises(r.SnapshotError):
                r.build_snapshot([bad], measured_on="2026-11-10")

    def test_serialize_roundtrip_one_row_per_line(self):
        snap = r.build_snapshot([_row(), _row(firm_key="66-40|X|Y")], measured_on="2026-11-10")
        text = r.serialize(snap)
        self.assertEqual(json.loads(text), snap)
        self.assertEqual(len(text.splitlines()), 2 + len(snap["listings"]))

    def test_committed_fixture_matches_schema(self):
        obj = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(obj["schema_version"], r.SCHEMA_VERSION)
        for row in obj["listings"]:
            self.assertEqual(set(row), set(r.FIELDS))
        self.assertEqual(r.build_snapshot(obj["listings"], measured_on=obj["measured_on"]), obj)


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.old = r.build_snapshot([_row(firm_key=f"66-40|K|{i}") for i in range(10)],
                                    measured_on="2026-11-01")

    def test_unchanged(self):
        new = r.build_snapshot([_row(firm_key=f"66-40|K|{i}") for i in range(10)],
                               measured_on="2026-11-10")
        self.assertEqual(r.guard(self.old, new), "unchanged")

    def test_drop_blocks(self):
        new = r.build_snapshot([_row(firm_key=f"66-40|K|{i}") for i in range(7)],
                               measured_on="2026-11-10")
        self.assertIsNotNone(r.guard(self.old, new))
        small = r.build_snapshot([_row(firm_key=f"66-40|K|{i}") for i in range(9)],
                                 measured_on="2026-11-10")
        self.assertIsNone(r.guard(self.old, small))

    def test_first_run(self):
        self.assertIsNone(r.guard(None, self.old))


class FetchAndMainTest(unittest.TestCase):
    def test_fetch_selects_whitelist_and_paginates(self):
        http = _Http([[_row()] * 2, [_row()]])
        rows = r.fetch_rows("https://x.supabase.co", "k", http=http, page_size=2)
        self.assertEqual(len(rows), 3)
        url, params = http.calls[0]
        self.assertTrue(url.endswith("/rest/v1/import_alert_listing"))
        self.assertEqual(params["select"].split(","), list(r.FIELDS))
        self.assertNotIn("address", params["select"])
        self.assertEqual(http.calls[1][1]["offset"], "2")

    def test_fetch_http_error(self):
        with self.assertRaises(RuntimeError):
            r.fetch_rows("https://x", "k", http=_Http([], status=401))

    def test_main_writes_then_unchanged_then_gate(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "import_alerts.json"
            args = ["--supabase-url", "https://x.supabase.co", "--service-role-key", "k",
                    "--out", str(out), "--measured-on", "2026-11-10"]
            rows = [_row(firm_key=f"66-40|K|{i}") for i in range(10)]
            self.assertEqual(r.main(args, fetch=lambda u, k: rows), 0)
            self.assertNotIn("Secret-ro", out.read_text(encoding="utf-8"))
            self.assertEqual(r.main(args, fetch=lambda u, k: rows), r.EXIT_NO_CHANGE)
            before = out.read_text(encoding="utf-8")
            self.assertEqual(r.main(args, fetch=lambda u, k: rows[:5]), 2)
            self.assertEqual(r.main(args, fetch=lambda u, k: []), 2)
            self.assertEqual(out.read_text(encoding="utf-8"), before)   # 실패는 파일 무변경

    def test_main_requires_credentials(self):
        import os
        saved = {k: os.environ.pop(k, None) for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
        try:
            self.assertEqual(r.main([], fetch=lambda u, k: [_row()]), 2)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
