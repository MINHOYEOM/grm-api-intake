"""기사 permalink 2차 dedup(2026-08-24) — ECA 가 같은 기사를 날짜만 바꿔 재게시하는 문제.

`_stable_doc_id` 가 `date_iso` 를 키에 넣으므로 재게시분은 doc_id 가 달라져 종전 dedup 을
구조적으로 빠져나간다(실측: ECA 126행 / 고유 기사 75개). 여기서는 ①재게시가 막히는가
②**막으면 안 되는 소스가 안 막히는가**(음성 검사 — 이쪽이 훨씬 중요하다. 공용 목록 URL 을
쓰는 MFDS·OpenFDA 에 이 dedup 이 걸리면 그 소스가 통째로 사라진다) 를 함께 고정한다.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_intake as ci  # noqa: E402
from grm_notion import canonical_url_key  # noqa: E402


def _item(source: str, doc_id: str, url: str, date_iso: str = "2026-08-17") -> ci.IntakeItem:
    return ci.IntakeItem(
        source=source, document_id=doc_id, date_iso=date_iso,
        headline="h", official_url=url)


def _insert(items, existing=None, url_keys=None):
    """notion_create_page 를 성공으로 스텁하고 insert_items 실행 → (inserted, skipped, failed)."""
    existing = set() if existing is None else existing
    with mock.patch.object(ci.time, "sleep"), \
            mock.patch.object(ci, "notion_create_page", return_value=True):
        return ci.insert_items(
            "tok", "db", items,
            date(2026, 8, 24),
            datetime(2026, 8, 24, tzinfo=timezone.utc),
            existing, False,
            existing_url_keys=url_keys,
            modality_enabled=False)


class CanonicalUrlKeyTest(unittest.TestCase):
    def test_scheme_host_lowercased_and_fragment_dropped(self) -> None:
        a = canonical_url_key("ECA Academy", "HTTPS://WWW.Gmp-Compliance.org/gmp-news/x")
        b = canonical_url_key("ECA Academy", "https://www.gmp-compliance.org/gmp-news/x#top")
        self.assertEqual(a, b)

    def test_single_trailing_slash_ignored(self) -> None:
        self.assertEqual(
            canonical_url_key("ECA Academy", "https://a.org/p/"),
            canonical_url_key("ECA Academy", "https://a.org/p"))

    def test_query_is_preserved(self) -> None:
        """query 를 지우면 `?id=1`·`?id=2` 가 한 키로 뭉쳐 서로 다른 문서가 유실된다."""
        self.assertNotEqual(
            canonical_url_key("X", "https://a.org/p?id=1"),
            canonical_url_key("X", "https://a.org/p?id=2"))

    def test_source_scoped(self) -> None:
        self.assertNotEqual(
            canonical_url_key("ECA Academy", "https://a.org/p"),
            canonical_url_key("ISPE", "https://a.org/p"))

    def test_empty_url_yields_empty_key(self) -> None:
        self.assertEqual(canonical_url_key("ECA Academy", ""), "")
        self.assertEqual(canonical_url_key("ECA Academy", "   "), "")


class AllowlistNegativeTest(unittest.TestCase):
    """⛔ 이 목록에 공용 목록 URL 소스가 들어가면 그 소스가 통째로 사라진다."""

    def test_shared_listing_url_sources_never_allowlisted(self) -> None:
        for src in ("MFDS", "OpenFDA Recall", "EMA", "FDA Warning Letter",
                    "FDA 483", "Health Canada Inspection"):
            self.assertNotIn(
                src, ci._CANONICAL_URL_DEDUP_SOURCES,
                f"{src} 는 기사 permalink 소스가 아니다 — 넣으면 수집이 무너진다")

    def test_allowlist_is_exactly_the_measured_article_feeds(self) -> None:
        self.assertEqual(ci._CANONICAL_URL_DEDUP_SOURCES, frozenset({"ECA Academy", "ISPE"}))


class RepublishDedupTest(unittest.TestCase):
    URL = "https://www.gmp-compliance.org/gmp-news/why-root-cause-analysis"

    def test_same_article_republished_with_new_date_is_skipped(self) -> None:
        """실사고 형태: 8/17 기사가 8/19 로 재게시 → doc_id 가 달라 1차 dedup 을 통과한다."""
        first = _item("ECA Academy", "02315b79a7d0", self.URL, "2026-08-17")
        again = _item("ECA Academy", "9791d0a07ca1", self.URL, "2026-08-19")
        self.assertNotEqual(first.document_id, again.document_id)

        url_keys: set[str] = set()
        self.assertEqual(_insert([first, again], url_keys=url_keys), (1, 1, 0))

    def test_cross_run_republish_skipped_via_prefetched_keys(self) -> None:
        """지난 실행에서 이미 넣은 기사 — Notion 조회로 받은 키 집합에 들어 있다."""
        prefetched = {canonical_url_key("ECA Academy", self.URL)}
        again = _item("ECA Academy", "9791d0a07ca1", self.URL, "2026-08-19")
        self.assertEqual(_insert([again], url_keys=prefetched), (0, 1, 0))

    def test_distinct_articles_both_inserted(self) -> None:
        a = _item("ECA Academy", "aaa", "https://www.gmp-compliance.org/gmp-news/a")
        b = _item("ECA Academy", "bbb", "https://www.gmp-compliance.org/gmp-news/b")
        self.assertEqual(_insert([a, b], url_keys=set()), (2, 0, 0))

    def test_non_allowlisted_source_sharing_one_url_is_not_deduped(self) -> None:
        """MFDS 는 1,760행이 고유 URL 27개를 공유한다 — 여기서 막히면 안 된다."""
        shared = "https://nedrug.mfds.go.kr/searchRecall"
        rows = [_item("MFDS", f"recall-{i}", shared) for i in range(5)]
        self.assertEqual(_insert(rows, url_keys=set()), (5, 0, 0))

    def test_none_url_keys_preserves_legacy_behaviour(self) -> None:
        """인자 미지정 호출부는 종전과 완전히 동일하게 동작한다(URL 중복도 그대로 들어간다)."""
        first = _item("ECA Academy", "id1", self.URL, "2026-08-17")
        again = _item("ECA Academy", "id2", self.URL, "2026-08-19")
        self.assertEqual(_insert([first, again], url_keys=None), (2, 0, 0))

    def test_doc_id_dedup_still_applies(self) -> None:
        it = _item("ECA Academy", "same", self.URL)
        self.assertEqual(_insert([it, it], url_keys=set()), (1, 1, 0))

    def test_item_without_official_url_is_not_blocked(self) -> None:
        """URL 이 없으면 2차 키가 빈 문자열 → 막지 않는다(빈 키로 뭉치면 유실)."""
        a = _item("ECA Academy", "n1", "")
        b = _item("ECA Academy", "n2", "")
        self.assertEqual(_insert([a, b], url_keys=set()), (2, 0, 0))


if __name__ == "__main__":
    unittest.main()
