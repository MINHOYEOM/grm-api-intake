"""deficiency_ko_apply.py — 지적 사례 한국어 칸(141) 채우기."""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import deficiency_ko_apply as ka  # noqa: E402

H1, H2 = "a" * 64, "b" * 64


def db_rows():
    return [
        {"source_key": "pmda:000283020", "row_no": 6, "statement": "Records were not kept.",
         "agency_category": "Document\nmanagement", "content_hash": H1,
         "statement_ko": "", "agency_category_ko": "", "ko_source_hash": ""},
        {"source_key": "pmda:000283020", "row_no": 7, "statement": "Changed original.",
         "agency_category": "Document management", "content_hash": H2,
         "statement_ko": "", "agency_category_ko": "", "ko_source_hash": ""},
        {"source_key": "mhra:2019_deficiency_data", "row_no": 2, "statement": "",
         "agency_category": "Chapter 1", "content_hash": H1,
         "statement_ko": "", "agency_category_ko": "", "ko_source_hash": ""},
        {"source_key": "mhra:2019_deficiency_data", "row_no": 3, "statement": "",
         "agency_category": "Chapter 1", "content_hash": H2,
         "statement_ko": "", "agency_category_ko": "", "ko_source_hash": ""},
    ]


TR = {"pmda:000283020#6": (H1, "기록을 보관하지 않았다."),
      "pmda:000283020#7": (H1, "원문이 바뀌기 전 번역")}
LABELS = {"Document management": "문서관리", "Chapter 1": "EU GMP 제1장"}


class PlanTest(unittest.TestCase):
    def test_plan_writes_fresh_skips_stale_and_groups_labels(self):
        patches, st = ka.plan(db_rows(), TR, LABELS)
        self.assertEqual(st["stmt_write"], 1)
        self.assertEqual(st["stale"], 1, "content_hash 가 다르면 쓰지 않는다")
        stmt = [p for p in patches if "statement_ko" in p[1]]
        self.assertEqual(stmt[0][0]["content_hash"], f"eq.{H1}")
        self.assertEqual(stmt[0][1], {"statement_ko": "기록을 보관하지 않았다.", "ko_source_hash": H1})
        lab = [p for p in patches if "agency_category_ko" in p[1]]
        # xlsx 두 행은 PATCH 하나, PMDA 는 원문 표기(줄바꿈 유무)마다 하나
        self.assertEqual(len(lab), 3)
        self.assertEqual(st["label_rows"], 4)
        self.assertIn(({"source_key": "eq.mhra:2019_deficiency_data", "agency_category": "eq.Chapter 1"},
                       {"agency_category_ko": "EU GMP 제1장"}), lab)

    def test_plan_is_idempotent(self):
        rows = db_rows()
        rows[0].update(statement_ko="기록을 보관하지 않았다.", ko_source_hash=H1)
        for r in rows:
            r["agency_category_ko"] = LABELS[ka.norm(r["agency_category"])]
        patches, st = ka.plan(rows, TR, LABELS)
        self.assertEqual(patches, [])
        self.assertEqual(st["stmt_same"], 1)

    def test_remaining(self):
        rows = db_rows()
        self.assertEqual(ka.remaining(rows), {"rows": 4, "statement_missing": 2, "category_missing": 4})
        rows[0].update(statement_ko="번역", ko_source_hash=H1)
        rows[1].update(statement_ko="번역", ko_source_hash=H1)  # 낡은 번역은 남은 행으로 센다
        self.assertEqual(ka.remaining(rows)["statement_missing"], 1)


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="grm_ko_apply_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, d):
        p = self.tmp / "ko.json"
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return p

    def test_load_ok(self):
        p = self._write({"schema": ka.SCHEMA, "rows": [{"id": "pmda:000283020#6", "chash": H1, "ko": "번역"}],
                         "labels": {"Document\nmanagement": "문서관리"}})
        rows, labels = ka.load(p)
        self.assertEqual(rows["pmda:000283020#6"], (H1, "번역"))
        self.assertEqual(labels, {"Document management": "문서관리"})

    def test_load_rejects(self):
        for bad in ({"schema": "x"},
                    {"schema": ka.SCHEMA, "rows": [{"id": "bad", "chash": H1, "ko": "번역"}]},
                    {"schema": ka.SCHEMA, "rows": [{"id": "pmda:000283020#6", "chash": H1, "ko": "no hangul"}]},
                    {"schema": ka.SCHEMA, "rows": [{"id": "pmda:000283020#6", "chash": H1, "ko": "번역"}] * 2},
                    {"schema": ka.SCHEMA, "labels": {"x": "english"}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ka.load(self._write(bad))


class MainTest(unittest.TestCase):
    def test_main_patches_then_reports_remaining(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grm_ko_main_"))
        state = db_rows()
        sent = []

        class Resp:
            def __init__(self, code, body=None):
                self.status_code, self._b, self.text = code, body, ""

            def json(self):
                return self._b

        class Http:
            def get(self, url, params, headers, timeout):
                return Resp(200, [dict(r) for r in state])

            def patch(self, url, params, headers, json, timeout):
                sent.append((params, json))
                for r in state:
                    if all(str(r.get(k)) == v[3:] for k, v in params.items()):
                        r.update(json)
                return Resp(204)

        try:
            p = tmp / "ko.json"
            p.write_text(json.dumps({"schema": ka.SCHEMA,
                                     "rows": [{"id": i, "chash": h, "ko": ko} for i, (h, ko) in TR.items()],
                                     "labels": LABELS}, ensure_ascii=False), encoding="utf-8")
            argv = ["--supabase-url", "https://x.supabase.co", "--service-role-key", "k", "--input", str(p)]
            self.assertEqual(ka.main(argv + ["--dry-run"], http=Http()), 0)
            self.assertEqual(sent, [])
            self.assertEqual(ka.main(argv, http=Http()), 3, "낡은 번역 1행이 남는다")
            self.assertEqual(state[0]["statement_ko"], "기록을 보관하지 않았다.")
            self.assertEqual(state[1]["statement_ko"], "")
            self.assertTrue(all(r["agency_category_ko"] for r in state))
            self.assertEqual(ka.main(argv + ["--input", str(tmp / "missing.json")], http=Http()), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
