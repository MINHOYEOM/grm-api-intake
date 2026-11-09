"""최근 규제 소식(news_page · news_refresh · 마이그 138) 표적 테스트 — 네트워크·DB 0."""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
ROOT = WEB.parent
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(ROOT))

import news_page as np_  # noqa: E402
import news_refresh as nr  # noqa: E402


def _row(source="EMA", kind="news", date="2026-11-03", title="T", url="https://x/a"):
    return {"source": source, "source_kind": kind, "published_date": date, "title": title, "official_url": url}


def _data(rows):
    snap, _ = nr.build_snapshot(rows, today="2026-11-10", days=60)
    return snap


class BuildSnapshotTest(unittest.TestCase):
    def test_sorted_newest_first_and_deterministic(self):
        rows = [_row(date="2026-10-01", title="B", url="https://x/1"),
                _row(date="2026-11-05", title="A", url="https://x/2"),
                _row(date="2026-11-05", source="ECA Academy", title="C", url="https://x/3")]
        a = _data(rows)
        b = _data(list(reversed(rows)))
        self.assertEqual(a, b)
        self.assertEqual([i["published_date"] for i in a["items"]], ["2026-11-05", "2026-11-05", "2026-10-01"])

    def test_rejects_unsafe_rows_and_counts_them(self):
        rows = [_row(title=" "), _row(url="ftp://x"), _row(url=""), _row(date="11/03"),
                _row(title="dup", url="https://x/d"), _row(title="dup", url="https://x/d"),
                _row(title="ok", url="https://x/ok")]
        snap, reject = nr.build_snapshot(rows, today="2026-11-10", days=60)
        self.assertEqual([i["title"] for i in snap["items"]], ["dup", "ok"])
        self.assertEqual(reject["제목 없음"], 1)
        self.assertEqual(reject["원문 링크 없음"], 2)
        self.assertEqual(reject["날짜 형식"], 1)
        self.assertEqual(reject["중복"], 1)

    def test_only_public_fields_are_kept(self):
        r = _row()
        r.update({"raw_json": "{}", "row_json": "{}", "firm_name": "비공개", "collected_at": "x"})
        snap, _ = nr.build_snapshot([r], today="2026-11-10", days=60)
        self.assertEqual(set(snap["items"][0]), set(nr.PUBLIC_FIELDS))

    def test_empty_input_main_refuses_to_write(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "rows.json"
            src.write_text("[]", encoding="utf-8")
            out = Path(d) / "news.json"
            rc = nr.main(["--supabase-url", "https://x", "--supabase-anon-key", "k",
                          "--from-json", str(src), "--out", str(out)])
            self.assertEqual(rc, 1)
            self.assertFalse(out.exists())


class ViewTest(unittest.TestCase):
    def test_missing_or_empty_gives_none(self):
        self.assertIsNone(np_.build_view(None, "ko"))
        self.assertIsNone(np_.build_view({"items": []}, "ko"))
        self.assertIsNone(np_.load_news(Path("/no/such/news.json")))

    def test_groups(self):
        g = np_.group_of
        self.assertEqual(g({"source": "OpenFDA Recall"}), "recall")
        self.assertEqual(g({"source": "Health Canada"}), "recall")
        self.assertEqual(g({"source": "MHRA Inspectorate", "source_kind": "Class 2 Medicines Recall"}), "recall")
        self.assertEqual(g({"source": "MHRA Inspectorate",
                            "source_kind": "Class 4 Medicines Defect Notification"}), "recall")
        self.assertEqual(g({"source": "MHRA Inspectorate", "source_kind": "Clinical Trials"}), "regulator")
        self.assertEqual(g({"source": "ECA Academy"}), "industry")
        self.assertEqual(g({"source": "ISPE"}), "industry")
        self.assertEqual(g({"source": "Federal Register"}), "gazette")
        self.assertEqual(g({"source": "MFDS"}), "regulator")
        self.assertEqual(g({"source": "새 소스"}), "regulator")

    def test_weeks_sorted_and_clipped(self):
        data = _data([_row(date="2026-11-05", title="x " * 300, url="https://x/1"),
                      _row(date="2026-10-20", url="https://x/2"),
                      _row(date="2026-11-03", url="https://x/3")])
        v = np_.build_view(data, "ko")
        self.assertEqual([w["start"] for w in v["weeks"]], ["2026-11-02", "2026-10-19"])
        self.assertLessEqual(len(v["weeks"][0]["items"][0]["title"]), np_.MAX_TITLE + 1)
        self.assertEqual(v["as_of"], "2026-11-05")
        self.assertEqual(v["total"], 3)

    def test_english_view_hides_korean_titles_and_counts_them(self):
        data = _data([_row(source="MFDS", kind="guidance-industry", title="가이드라인", url="https://x/k"),
                      _row(title="Guideline", url="https://x/e")])
        ko = np_.build_view(data, "ko")
        en = np_.build_view(data, "en")
        self.assertEqual((ko["total"], ko["hidden_ko"]), (2, 0))
        self.assertEqual((en["total"], en["hidden_ko"]), (1, 1))
        for w in en["weeks"]:
            for it in w["items"]:
                self.assertIsNone(re.search("[가-힣]", it["source"] + it["title"]))
        self.assertIsNone(re.search("[가-힣]", "".join(g["label"] for g in en["groups"])))

    def test_all_korean_english_view_is_none(self):
        data = _data([_row(source="MFDS", kind="guidance-industry", title="가이드라인")])
        self.assertIsNone(np_.build_view(data, "en"))


class SeededSnapshotTest(unittest.TestCase):
    """커밋된 정본(web/data/news.json)이 계약을 지킨다."""

    @classmethod
    def setUpClass(cls):
        cls.data = np_.load_news()

    def test_seed_present_and_sorted(self):
        self.assertIsNotNone(self.data)
        dates = [i["published_date"] for i in self.data["items"]]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertEqual(self.data["totals"]["items"], len(self.data["items"]))

    def test_links_valid_and_fields_public_only(self):
        for i in self.data["items"]:
            self.assertEqual(set(i), set(nr.PUBLIC_FIELDS))
            self.assertRegex(i["official_url"], r"^https?://\S+$")
            self.assertTrue(i["title"].strip())

    def test_every_source_has_label_and_group(self):
        for src in {i["source"] for i in self.data["items"]}:
            self.assertIn(src, np_.SOURCE_LABELS, f"소스 라벨 누락: {src}")
            self.assertIn(np_.group_of({"source": src, "source_kind": ""}), np_.GROUP_LABELS)

    def test_english_labels_have_no_hangul(self):
        for _ko, en in list(np_.SOURCE_LABELS.values()) + list(np_.GROUP_LABELS.values()):
            self.assertIsNone(re.search("[가-힣]", en))


class MigrationContractTest(unittest.TestCase):
    """138 RPC: 공개 안전 5컬럼만 · anon 명시 회수 후 재부여 · 범위 밖/findings 연결분 제외."""

    @classmethod
    def setUpClass(cls):
        cls.sql = (WEB / "migrations" / "138_regulatory_news_public.sql").read_text(encoding="utf-8")
        cls.body = cls.sql.split("as $$", 1)[1].split("$$;", 1)[0]

    def test_returns_only_public_columns(self):
        m = re.search(r"returns table \(([^)]*)\)", self.sql)
        cols = [c.strip().split()[0] for c in m.group(1).split(",")]
        self.assertEqual(cols, list(nr.PUBLIC_FIELDS))
        head = self.body.split("from public.raw_signals", 1)[0]
        for secret in ("raw_json", "row_json", "firm_name", "site_name", "collected_at", "raw_signal_id"):
            self.assertNotIn(secret, head)

    def test_filters(self):
        for needle in ("'Unrelated'", "public.findings", "official_url ~* '^https?://'", "limit 3000"):
            self.assertIn(needle, self.body)

    def test_security_definer_and_grants(self):
        self.assertIn("security definer", self.sql)
        self.assertIn("set search_path = public", self.sql)
        self.assertIn("revoke all on function public.regulatory_news(integer) from public, anon, authenticated;",
                      self.sql)
        self.assertIn("grant execute on function public.regulatory_news(integer) to anon, authenticated, service_role;",
                      self.sql)

    def test_source_allowlist_matches_script_labels(self):
        """SQL 허용목록의 소스는 모두 화면 라벨이 있다 — 새 소스를 SQL 에만 넣으면 여기서 실패."""
        names = set(re.findall(r"'([^']+)'", self.body.split("rs.source in (", 1)[1].split(")", 1)[0]))
        self.assertTrue(names)
        self.assertEqual(names - set(np_.SOURCE_LABELS), set())

    def test_migration_number_not_duplicated(self):
        nums = [p.name[:3] for p in (WEB / "migrations").glob("[0-9][0-9][0-9]_*.sql")]
        self.assertEqual(nums.count("138"), 1)


class WorkflowTest(unittest.TestCase):
    def test_workflow_pins_actions_and_uses_anon_key(self):
        wf = (ROOT / ".github" / "workflows" / "grm-news-refresh.yml").read_text(encoding="utf-8")
        for m in re.finditer(r"uses:\s*(\S+)@(\S+)", wf):
            self.assertRegex(m.group(2), r"^[0-9a-f]{40}$", m.group(0))
        self.assertIn("SUPABASE_ANON_KEY", wf)
        self.assertNotIn("SERVICE_ROLE", wf.upper())


if __name__ == "__main__":
    unittest.main()
