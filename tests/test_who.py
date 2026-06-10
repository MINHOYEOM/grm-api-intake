"""WHO 수집기 회귀 — B4(NOC 침묵 0건 금지: 구조 sentinel + 별칭 선택자).

NOC 선택이 /prequal/node/N 패턴+연도 게이트뿐이라 URL 스킴 변경 시 전건
탈락 → ([], None) 침묵 0건이 가능했다(NOC = Tier 3 최고신호). B4:
- 후보 확장: 'notice' 포함 /prequal/ 별칭 href 도 수용(연도 게이트가 nav 차단)
- sentinel: items 0 일 때 ① prequal 앵커 0 = 렌더 이상 error,
  ② 연도 텍스트 콘텐츠 앵커가 패턴 밖 = 스킴 변경 의심 error,
  ③ 연도 콘텐츠 앵커 자체가 없음 = 진짜 빈 목록(0건 정상).
- NOC core 승격: sentinel error 가 collect_who() error 로 전파(종전 core=False
  는 RSS/WHOPIR 정상 시 NOC 오류가 로그로만 남아 health 에 묻혔다).

fixture 의 nav/엔트리 형태는 2026-06-10 라이브 페이지에서 채록.
"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_who as w

RUN = date(2026, 6, 10)

# 라이브 페이지의 nav 축약 — 'notice' 별칭 링크들은 전부 연도 없음(메뉴).
_NAV = """
<a href="/prequal/inspection-services/notice-concern">Notice of Concern</a>
<a href="/prequal/inspection-services/notices-concern-nocs-medicines">NOC - Medicines</a>
<a href="/prequal/inspection-services/who-public-inspection-reports-whopirs-medicines">WHOPIR - Medicines</a>
<a href="/prequal/about-us">About us</a>
"""
_NOC_NODE_ENTRY = ('<a href="/prequal/node/828">Panexcell Clinical Lab Pvt Ltd, '
                   'Navi Mumbai - INDIA (09 October 2020)</a>')


class _StubHtml:
    def __init__(self, html: str):
        self.html = html
        self.urls: list[str] = []

    def __call__(self, url: str, **kwargs) -> str:
        self.urls.append(url)
        return self.html


class _Patched:
    """_get_html 스텁 + 요청 딜레이 0 (무네트워크·고속)."""

    def __init__(self, html: str):
        self.stub = _StubHtml(html)

    def __enter__(self):
        self._orig_get = w._get_html
        self._orig_delay = w.REQUEST_DELAY_SECONDS
        w._get_html = self.stub
        w.REQUEST_DELAY_SECONDS = 0
        return self.stub

    def __exit__(self, *exc):
        w._get_html = self._orig_get
        w.REQUEST_DELAY_SECONDS = self._orig_delay
        return False


class NocSelectorTest(unittest.TestCase):
    def test_node_entry_collected(self) -> None:
        # (a) 정상 렌더 + node 링크 → 항목 추출(현행 스킴 동결).
        with _Patched(_NAV + _NOC_NODE_ENTRY):
            items, err = w._collect_noc(RUN)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it.date_iso, "2020-10-09")
        self.assertEqual(it.signal_tier, "Tier 3")
        self.assertEqual(it.official_url, "https://extranet.who.int/prequal/node/828")
        self.assertIn("Panexcell", it.headline)

    def test_alias_scheme_entry_collected(self) -> None:
        # (a') B4 선택자 확장: /node/N 이 별칭 경로로 바뀌어도 연도 텍스트 엔트리 수집.
        html = _NAV + ('<a href="/prequal/inspection-services/notice-concern/'
                       'panexcell-clinical-lab">Panexcell Clinical Lab Pvt Ltd '
                       '(09 October 2020)</a>')
        with _Patched(html):
            items, err = w._collect_noc(RUN)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].date_iso, "2020-10-09")

    def test_nav_menu_links_not_collected(self) -> None:
        # nav 'notice' 메뉴(연도 없음)는 별칭 후보라도 연도 게이트가 차단.
        with _Patched(_NAV + _NOC_NODE_ENTRY):
            items, _ = w._collect_noc(RUN)
        self.assertEqual(len(items), 1)               # nav 4링크는 항목화 금지

    def test_node_plus_alias_same_noc_deduped(self) -> None:
        # 같은 NOC 를 node 와 별칭이 동시에 가리키면(티저+제목) 1건만.
        html = (_NAV + _NOC_NODE_ENTRY
                + '<a href="/prequal/inspection-services/notice-concern/x">'
                  'Panexcell Clinical Lab Pvt Ltd, Navi Mumbai - INDIA '
                  '(09 October 2020)</a>')
        with _Patched(html):
            items, err = w._collect_noc(RUN)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)


class NocSentinelTest(unittest.TestCase):
    def test_scheme_change_zero_items_is_error(self) -> None:
        # (b) 렌더 정상(prequal 앵커 존재) + 연도 콘텐츠 앵커가 패턴 밖 → error.
        html = _NAV + ('<a href="/prequal/inspection-services/entries/panexcell">'
                       'Panexcell Clinical Lab (09 October 2020)</a>')
        with _Patched(html):
            items, err = w._collect_noc(RUN)
        self.assertEqual(items, [])
        self.assertIsNotNone(err)
        self.assertIn("스킴 변경 의심", err)
        self.assertIn("수동 확인 필요", err)

    def test_truly_empty_list_is_normal_zero(self) -> None:
        # (c) 렌더 정상 + 연도 콘텐츠 앵커 자체가 없음 = 진짜 빈 목록 → 0건 정상.
        with _Patched(_NAV):
            items, err = w._collect_noc(RUN)
        self.assertEqual(items, [])
        self.assertIsNone(err)

    def test_blank_or_foreign_page_is_error(self) -> None:
        # 렌더 이상: prequal 앵커가 하나도 없는 200 응답(WAF 중간페이지 등) → error.
        with _Patched("<html><body><p>Service temporarily unavailable</p></body></html>"):
            items, err = w._collect_noc(RUN)
        self.assertEqual(items, [])
        self.assertIsNotNone(err)
        self.assertIn("렌더 이상", err)


class NocCorePropagationTest(unittest.TestCase):
    def test_noc_sentinel_error_propagates_to_collect_who(self) -> None:
        # B4 core 승격: RSS/WHOPIR 정상이어도 NOC 구조 error 가 소스 error 로 전파
        # (종전 core=False 는 WARN 로그로만 남아 health 에 묻혔다).
        orig = (w._collect_rss, w._collect_whopir, w._collect_noc)
        w._collect_rss = lambda s, e: ([], None)
        w._collect_whopir = lambda e: ([_dummy_item("whopir")], None)
        w._collect_noc = lambda e: ([], "WHO NOC 선택자 0건 — URL 스킴 변경 의심(수동 확인 필요)")
        try:
            items, err = w.collect_who(date(2026, 6, 1), date(2026, 6, 8))
        finally:
            w._collect_rss, w._collect_whopir, w._collect_noc = orig
        self.assertEqual(len(items), 1)               # 수집분은 graceful 반환
        self.assertIsNotNone(err)
        self.assertIn("NOC", err)

    def test_all_channels_ok_no_error(self) -> None:
        orig = (w._collect_rss, w._collect_whopir, w._collect_noc)
        w._collect_rss = lambda s, e: ([], None)
        w._collect_whopir = lambda e: ([_dummy_item("whopir")], None)
        w._collect_noc = lambda e: ([], None)         # 진짜 0건 → 정상
        try:
            items, err = w.collect_who(date(2026, 6, 1), date(2026, 6, 8))
        finally:
            w._collect_rss, w._collect_whopir, w._collect_noc = orig
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)


def _dummy_item(tag: str):
    return w.IntakeItem(
        source=w.SOURCE_WHO, document_id=f"who-test-{tag}", date_iso="2026-06-04",
        headline=f"[{tag}] t", official_url="https://extranet.who.int/x",
        type_or_class=w.TYPE_WHO_INSPECTION, firm="f", body="b",
        api_query="q", qa_relevance="Likely", osd_relevance="N/A",
        source_type=w.SRC_TYPE_OFFICIAL_PAGE, signal_tier="Tier 2",
        raw_payload={}, source_url="s", language=w.LANGUAGE_EN,
        region_jurisdiction=w.REGION_WHO,
    )


if __name__ == "__main__":
    unittest.main()
