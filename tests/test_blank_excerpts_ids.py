"""수집 완전성 N3 — WHO·EMA·ECA·MHRA 원문 빈칸 번호 지정 재수집(`backfill_blank_excerpts --ids`).

합격 조건: 칸 대조가 결손으로 찍은 번호를 넣으면 그 행만 다시 읽어 채우고, 다음 실행(같은 번호)은
`already_filled` 로 정상 닫힌다. 못 메운 번호(빈 판독·DB 에 없음)는 exit 1 로 경보가 된다.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

import backfill_blank_excerpts as b

ENV = {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "k"}
LONG = "x" * 300 + " END"


def row(doc: str, excerpt: str = "") -> dict:
    raw = {"title": doc}
    if excerpt:
        raw["eca_article_excerpt"] = excerpt
    return {"source": b.SOURCE_ECA, "document_id": doc, "official_url": f"https://eca.example/{doc}",
            "raw_signal_id": f"rs-{doc}", "raw_sha256": "h", "raw_json": json.dumps(raw),
            "row_json": json.dumps({}), "ingested_at": "2026-11-20T00:00:00Z"}


def run_main(argv, rows, *, article=LONG):
    out = {}

    def fake_write(*_a, **_k):
        out["writes"] = out.get("writes", 0) + 1

    with patch.dict("os.environ", ENV), \
            patch.object(b.fsb, "_fetch_all_pages", return_value=rows) as fetch, \
            patch.object(b, "_read_article", Mock(return_value=article)), \
            patch.object(b.rv, "replace_raw", side_effect=fake_write), \
            patch("sys.stdout") as stdout:
        code = b.main(argv)
    printed = "".join(c.args[0] for c in stdout.write.call_args_list if c.args)
    payload = json.loads(printed[printed.index("{"):]) if "{" in printed else {}
    return code, payload, fetch, out.get("writes", 0)


class IdsModeTest(unittest.TestCase):
    def test_blank_row_filled_then_next_run_closes(self):
        # 결손 주입: ECA 행 B 의 발췌가 비어 있다.
        code, payload, fetch, writes = run_main(["--source", "eca", "--ids", "B", "--apply"], [row("B")])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["ids"], {"B": "complete"})
        self.assertEqual(writes, 1)
        self.assertEqual(fetch.call_args.kwargs["extra_params"]["document_id"], 'in.("B")')
        # 다음 실행 — 이제 칸이 차 있으면 쓰기 없이 정상.
        code2, payload2, _f, writes2 = run_main(["--source", "eca", "--ids", "B", "--apply"],
                                                 [row("B", excerpt=LONG)])
        self.assertEqual(code2, 0)
        self.assertEqual(payload2["ids"], {"B": "already_filled"})
        self.assertEqual(writes2, 0)

    def test_empty_read_alarms(self):
        code, payload, _f, writes = run_main(["--source", "eca", "--ids", "B", "--apply"], [row("B")],
                                             article="")
        self.assertEqual(code, 1)
        self.assertEqual(payload["ids"], {"B": "empty_excerpt"})
        self.assertEqual(writes, 0)

    def test_not_in_db_alarms(self):
        code, payload, _f, _w = run_main(["--source", "eca", "--ids", "B,C"], [row("B")])
        self.assertEqual(code, 1)
        self.assertEqual(payload["ids"]["C"], "not_found")
        self.assertEqual(payload["ids"]["B"], "would_apply")

    def test_dry_run_would_apply_is_ok_and_writes_nothing(self):
        code, payload, _f, writes = run_main(["--source", "eca", "--ids", "B"], [row("B")])
        self.assertEqual(code, 0)
        self.assertEqual(writes, 0)

    def test_without_ids_behaviour_unchanged(self):
        code, payload, fetch, _w = run_main(["--source", "eca"], [])
        self.assertEqual(code, 0)
        self.assertNotIn("ids", payload)
        self.assertNotIn("document_id", fetch.call_args.kwargs["extra_params"])


class HelpersTest(unittest.TestCase):
    def test_parse_ids_strips_source_prefix(self):
        self.assertEqual(b.parse_ids("ECA Academy::a1, a1\nb2"), ["a1", "b2"])

    def test_ids_filter_escapes(self):
        self.assertEqual(b.ids_filter(['a"b', "c\\d"]), 'in.("a\\"b","c\\\\d")')

    def test_too_many_ids_rejected(self):
        ids = ",".join(f"d{i}" for i in range(b.IDS_MAX + 1))
        with patch.dict("os.environ", ENV), patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                b.main(["--ids", ids])


if __name__ == "__main__":
    unittest.main()
