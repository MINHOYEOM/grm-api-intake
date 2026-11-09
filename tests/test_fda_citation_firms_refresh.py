"""fda_citation_firms_refresh.py — 제조소 단위 집계·검증·게이트 단위 테스트(네트워크 없음)."""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import fda_citation_firms_refresh as r  # noqa: E402


def _row(iid="I1", cid="C1", code="21 CFR 211.192", date="2025-03-04", **kw):
    base = {"inspection_id": iid, "citation_id": cid, "fei_number": "3001",
            "legal_name": "Alpha Pharma Limited", "city": "Hyderabad", "state_code": "",
            "country_name": "India", "country_key": "IN", "inspection_end_date": date,
            "act_cfr_number": code, "short_description": "Investigations of discrepancies",
            "ingested_at": "2026-09-25T01:02:03+00:00",
            # 원장에 있는 비공개·불필요 열 — 스냅샷에 실리면 안 된다.
            "long_description": "SECRET BODY TEXT", "zip_code": "500001"}
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
    def test_counts_inspections_not_rows(self):
        rows = [_row(cid="C1", code="21 CFR 211.100(a)"),
                _row(cid="C2", code="21 CFR 211.100(a)"),        # 같은 실사 같은 표기 두 줄 → 1
                _row(cid="C3", code="21 CFR 211.100(b)"),
                _row(iid="I2", cid="C4", code="21 CFR 211.100(a)", date="2026-01-10")]
        snap = r.build_snapshot(rows)
        self.assertEqual(r.validate(snap), [])
        f = snap["firms"][0]
        self.assertEqual(f["inspections"], 2)
        self.assertEqual((f["first"], f["last"]), ("2025-03-04", "2026-01-10"))
        self.assertEqual(f["codes"], [["21 CFR 211.100(a)", 2, "2026-01-10"],
                                      ["21 CFR 211.100(b)", 1, "2025-03-04"]])
        # 섹션은 하위항을 합쳐 실사 수로 센다(I1 은 (a)·(b) 둘 다 인용해도 1).
        self.assertEqual(f["sections"], [["211.100", 2, "2026-01-10"]])
        self.assertEqual(snap["as_of"], "2026-09-25")
        self.assertEqual(snap["totals"], {"firms": 1, "inspections": 2, "codes": 2})

    def test_non_cfr_citations_dropped_and_no_private_fields(self):
        rows = [_row(), _row(cid="C2", code="FD&C Act 501(a)(2)(B)")]
        snap = r.build_snapshot(rows)
        self.assertEqual([c[0] for c in snap["firms"][0]["codes"]], ["21 CFR 211.192"])
        text = r.serialize(snap)
        for leaked in ("SECRET", "500001", "long_description", "zip", "I1", "C1", "FD&C"):
            self.assertNotIn(leaked, text)

    def test_site_is_fei_plus_name(self):
        rows = [_row(), _row(iid="I2", fei_number="3002", city="Vizag"),
                _row(iid="I3", legal_name="Alpha Pharma Ltd")]
        snap = r.build_snapshot(rows)
        self.assertEqual([(f["fei"], f["name"]) for f in snap["firms"]],
                         [("3001", "Alpha Pharma Limited"), ("3001", "Alpha Pharma Ltd"),
                          ("3002", "Alpha Pharma Limited")])

    def test_title_is_most_frequent_then_alpha(self):
        rows = [_row(short_description="B"), _row(iid="I2", short_description="A"),
                _row(iid="I3", short_description="B")]
        self.assertEqual(r.build_snapshot(rows)["titles"]["21 CFR 211.192"], "B")

    def test_rejects_empty_and_bad_rows(self):
        with self.assertRaises(r.SnapshotError):
            r.build_snapshot([])
        with self.assertRaises(r.SnapshotError):
            r.build_snapshot([_row(code="FD&C Act 501")])            # 21 CFR 0건
        for bad in (_row(legal_name=" "), _row(inspection_id=""),
                    _row(inspection_end_date=None), _row(inspection_end_date="03/04/2025")):
            with self.assertRaises(r.SnapshotError):
                r.build_snapshot([bad])

    def test_serialize_roundtrip_one_site_per_line(self):
        snap = r.build_snapshot([_row(), _row(iid="I2", fei_number="9")])
        text = r.serialize(snap)
        self.assertEqual(json.loads(text), snap)
        self.assertEqual(len(text.splitlines()), 2 + len(snap["firms"]))


class ValidateGuardTest(unittest.TestCase):
    def setUp(self):
        self.snap = r.build_snapshot([_row(iid=f"I{i}", fei_number=str(i)) for i in range(10)])

    def test_validate_catches_shape(self):
        self.assertEqual(r.validate({"schema_version": "x"}), ["schema_version"])
        bad = json.loads(json.dumps(self.snap))
        bad["firms"][0]["codes"][0][1] = 99                         # 실사 수 초과
        self.assertTrue(r.validate(bad))
        bad = json.loads(json.dumps(self.snap))
        bad["as_of"] = "soon"
        self.assertIn("as_of", r.validate(bad))

    def test_unchanged_drop_and_ok(self):
        same = json.loads(json.dumps(self.snap))
        same["as_of"] = "2030-01-01"
        self.assertEqual(r.guard(self.snap, same), "unchanged")
        small = r.build_snapshot([_row(iid=f"I{i}", fei_number=str(i)) for i in range(7)])
        self.assertIn("감소", r.guard(self.snap, small, max_drop_pct=20))
        self.assertIsNone(r.guard(self.snap, small, max_drop_pct=50))
        self.assertIsNone(r.guard(None, small))


class FetchAndMainTest(unittest.TestCase):
    def test_fetch_pages_and_select_whitelist(self):
        http = _Http([[{"a": 1}, {"a": 2}], [{"a": 3}]])
        rows = r.fetch_rows("https://x.supabase.co", "k", http=http, page_size=2)
        self.assertEqual(len(rows), 3)
        sel = http.calls[0][1]["select"]
        self.assertNotIn("long_description", sel)
        self.assertNotIn("zip_code", sel)
        with self.assertRaises(RuntimeError):
            r.fetch_rows("https://x", "k", http=_Http([[]], status=401))

    def test_main_writes_then_no_change_then_gate(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "s.json"
            args = ["--supabase-url", "https://x.supabase.co", "--service-role-key", "k",
                    "--out", str(out)]
            rows = [_row(iid=f"I{i}", fei_number=str(i)) for i in range(10)]
            self.assertEqual(r.main(args, fetch=lambda b, k: rows), 0)
            self.assertEqual(r.validate(json.loads(out.read_text(encoding="utf-8"))), [])
            self.assertEqual(r.main(args, fetch=lambda b, k: rows), r.EXIT_NO_CHANGE)
            before = out.read_bytes()
            self.assertEqual(r.main(args, fetch=lambda b, k: rows[:5]), 2)
            self.assertEqual(r.main(args, fetch=lambda b, k: []), 2)
            self.assertEqual(out.read_bytes(), before)               # 게이트 위반은 파일 무변경

    def test_main_requires_credentials(self):
        import os
        saved = {k: os.environ.pop(k, None) for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
        try:
            self.assertEqual(r.main(["--out", "unused.json"]), 2)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
