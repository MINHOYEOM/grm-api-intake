#!/usr/bin/env python3
"""[노션 은퇴 게이트] GRM_INTAKE_NOTION=off 에서 발행 길(수집기·브릿지·데스크·교열 마감·종합상황실 판정)이
노션을 한 번도 부르지 않는가 — 매 CI 에서 확인한다.

실행 검사(노션 API 를 부르면 바로 실패하게 막고 돌린다)는 길마다 따로 있다:
  · 수집기 — tests/test_intake_notion_off.py (`off_makes_zero_notion_calls…`)
  · 원천 대조 — tests/test_notion_off_surroundings.py
  · 발행 브릿지·FDA 회수 뒤 handoff 상태 — tests/test_bridge_notion_off.py
이 파일은 그 위에 **빠진 곳이 없는지**를 지킨다:
  1. 워크플로 전수 — `NOTION_TOKEN` 을 받는 단계는 전부 아래 셋 중 하나여야 한다. 새 사용처가 생기면 여기서 빨개진다.
     (a) `if:` 에 `vars.GRM_INTAKE_NOTION != 'off'` 가 있어 off 면 돌지 않는다.
     (b) off 를 스스로 아는 스크립트(OFF_AWARE)를 부르고, 같은 단계·잡에 변수를 넘긴다.
     (c) 발행 길 밖의 도구로, 이유를 적어 EXEMPT 에 올렸다(노션을 '옛 자료'로 읽기만 한다).
  2. OFF_AWARE 스크립트가 실제로 같은 판정 함수(`grm_queue.notion_intake_off`)를 쓴다.
  3. 실행 검사 파일이 있고, 거기에 '노션을 부르면 실패' 장치가 있다(누가 지우면 빨개진다).
  4. 사람(클라우드 Routine)이 읽는 발행 길 지시문이 노션 확인·CONSUMED 마감·'백업이 발행'에 기대지 않는다.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WF = ROOT / ".github" / "workflows"

SWITCH_PASS = "GRM_INTAKE_NOTION: ${{ vars.GRM_INTAKE_NOTION || 'on' }}"
SWITCH_SKIP = "vars.GRM_INTAKE_NOTION != 'off'"

# off 를 스스로 아는 스크립트 — off 면 노션 API 를 부르지 않는다(실행 검사: 위 docstring).
OFF_AWARE = {
    "collect_intake.py": "tests/test_intake_notion_off.py",
    "delta_bridge.py": "tests/test_bridge_notion_off.py",
    "fda_body_coverage.py": "tests/test_bridge_notion_off.py",
    "source_reconcile.py": "tests/test_notion_off_surroundings.py",
}

# 발행 길 밖 — 노션을 옛 자료로 **읽기만** 한다. off 뒤에도 토큰이 살아 있는 동안 읽을 수 있고, 발행을 막지 않는다.
# 노션을 완전히 지우는 5단계(GRM_노션은퇴목록 §7)에서 이 목록이 0 이 된다.
EXEMPT = {
    ("grm-brief-audit.yml", "verify_published_brief.py"):
        "발행 뒤 감사. 웹 발행본(brief_web_*.json)이 있으면 노션을 보지 않고, 없을 때만 옛 Weekly Brief 를 읽는다",
    ("grm-fda483-body-full-backfill.yml", "backfill_483_body_full.py"):
        "483 전문 백필. 옛 노션 행 본문을 첫째 원천으로 읽기만 한다(없으면 PDF)",
    ("grm-intake-queue.yml", "grm_queue.py"):
        "수동 실행 전용(workflow_dispatch). backfill-history 만 옛 노션을 읽는다",
    ("grm-raw-signals-notion-backfill.yml", "backfill_raw_signals_from_notion.py"):
        "수동 실행 전용. 옛 노션 행 → raw_signals 백필(읽기만)",
}

# 실패 알림처럼 파이썬을 부르지 않는 단계가 잡 env 의 토큰을 물려받는 경우 — 노션 API 를 부르지 않는다.
NO_SCRIPT_OK = re.compile(r"(Open|issue|warning|경고)", re.I)


def _steps(text: str) -> "tuple[str, list[tuple[str, str]]]":
    """(steps 앞부분, [(이름, 단계 본문)]) — `- name:` 기준으로 자른다(이 저장소 워크플로는 모든 단계에 name 이 있다)."""
    lines = text.splitlines()
    starts = [i for i, ln in enumerate(lines) if re.match(r"^\s+- name:", ln)]
    head = "\n".join(lines[: starts[0]] if starts else lines)
    out = []
    for k, a in enumerate(starts):
        b = starts[k + 1] if k + 1 < len(starts) else len(lines)
        name = lines[a].split("name:", 1)[1].strip()
        out.append((name, "\n".join(lines[a:b])))
    return head, out


def _if_line(block: str) -> str:
    for ln in block.splitlines():
        if ln.strip().startswith("if:"):
            return ln.strip()
    return ""


class WorkflowInventoryTest(unittest.TestCase):
    def test_every_notion_token_step_is_classified(self) -> None:
        seen_exempt = set()
        problems = []
        for path in sorted(WF.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            if "NOTION_TOKEN" not in text:
                continue
            head, steps = _steps(text)
            job_token = "NOTION_TOKEN" in head
            for name, block in steps:
                if "NOTION_TOKEN" not in block and not job_token:
                    continue
                scripts = sorted(set(re.findall(r"python3? ([\w./-]+\.py)", block)))
                cond = _if_line(block)
                where = f"{path.name} · {name}"
                if SWITCH_SKIP in cond:
                    continue                                                    # (a)
                if not scripts:
                    if "api.notion.com" not in block and "NOTION_TOKEN" not in block:
                        continue                                                # 잡 env 를 물려받았을 뿐 노션을 부르지 않는 단계
                    if "api.notion.com" not in block and NO_SCRIPT_OK.search(name):
                        continue                                                # 실패 알림 단계(gh 이슈만 연다)
                    if "steps.bridge.outputs.source != 'git'" in cond:
                        continue                                                # 아래 CONSUMED 시험이 따로 본다
                    problems.append(f"{where}: 노션 토큰을 받는데 스크립트·off 조건이 없다")
                    continue
                for s in scripts:
                    base = Path(s).name
                    if base in OFF_AWARE:
                        if SWITCH_PASS not in block and SWITCH_PASS not in head:
                            problems.append(f"{where}: {base} 는 off 를 알지만 변수 GRM_INTAKE_NOTION 을 넘기지 않는다")
                        continue                                                # (b)
                    if (path.name, base) in EXEMPT:
                        seen_exempt.add((path.name, base))
                        continue                                                # (c)
                    if "steps.bridge.outputs.source != 'git'" in cond and base == "delta_bridge.py":
                        continue
                    problems.append(f"{where}: {base} — off 에서 노션을 부를 수 있다. "
                                    "off 조건을 걸거나(OFF_AWARE·SWITCH_SKIP) 이유와 함께 EXEMPT 에 올려라")
        self.assertEqual(problems, [], "\n".join(problems))
        stale = sorted(set(EXEMPT) - seen_exempt)
        self.assertEqual(stale, [], f"EXEMPT 에 더는 노션을 받지 않는 항목이 남았다(지워라): {stale}")

    def test_consume_step_never_runs_when_off(self) -> None:
        """CONSUMED 마감 단계가 남아 있다면 노션에서 읽은 호(source≠git)에만 돈다 — off 면 브릿지가 git 만 낸다."""
        text = (WF / "grm-delta-bridge.yml").read_text(encoding="utf-8")
        _, steps = _steps(text)
        for name, block in steps:
            if "--consume" in block:
                cond = _if_line(block)
                self.assertTrue("steps.bridge.outputs.source != 'git'" in cond or SWITCH_SKIP in cond, name)

    def test_off_aware_scripts_use_the_one_switch(self) -> None:
        for script, test in OFF_AWARE.items():
            with self.subTest(script=script):
                src = (ROOT / script).read_text(encoding="utf-8")
                self.assertRegex(src, r"notion_intake_off\(\)|\"GRM_INTAKE_NOTION\"", script)
                t = (ROOT / test).read_text(encoding="utf-8")
                self.assertIn("off", t)
                self.assertRegex(t, r"(AssertionError|side_effect=|assert_not_called|노션 호출 금지)", test)


class RuntimeGuardsPresentTest(unittest.TestCase):
    """실행 검사가 '노션을 부르면 실패' 장치를 잃지 않았는가."""

    def test_bridge_test_blocks_the_notion_api(self) -> None:
        t = (ROOT / "tests" / "test_bridge_notion_off.py").read_text(encoding="utf-8")
        self.assertIn('mock.patch.object(db, "notion_api_request", side_effect=_no_notion)', t)
        self.assertIn('mock.patch.object(db, "fetch_handoff_body_full", side_effect=_no_notion)', t)

    def test_collector_test_counts_zero_notion_calls(self) -> None:
        t = (ROOT / "tests" / "test_intake_notion_off.py").read_text(encoding="utf-8")
        self.assertIn("def test_off_makes_zero_notion_calls_and_runs_on_the_database", t)


class PublishPathInstructionsTest(unittest.TestCase):
    """클라우드 Routine 이 읽는 발행 길 지시문 — off 뒤 노션을 읽거나 쓰라고 하지 않는다."""

    FILES = [("company", "editorial", f) for f in ("RUN.md", "desk.md", "reporter.md", "copyedit.md")] + \
            [("company", "editorial", "next", f) for f in ("RUN.md", "desk.md", "reporter.md", "copyedit.md")] + \
            [("company", "situation-room", "brief-prep.md")]
    FORBIDDEN = (
        "CONSUMED` 로 바꾼다",          # 교열의 노션 마감
        "OPEN(Status=New)",             # 데스크의 노션 OPEN 확인
        "백업이 발행",                   # 꺼진 v16 백업에 기대는 문장
        "백업 Routine 이 발행",
        "09:30 백업",
        "collection://",                # 노션 data source 조회
        "`CONSUMED` 를 찍",
        "`CONSUMED` 뒤에",
        "반납 길: notion` 인 호는 종전 순서",
    )

    def test_no_notion_dependence(self) -> None:
        problems = []
        for parts in self.FILES:
            text = ROOT.joinpath(*parts).read_text(encoding="utf-8")
            for phrase in self.FORBIDDEN:
                if phrase in text:
                    problems.append(f"{'/'.join(parts)}: {phrase!r}")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_live_and_next_copies_say_the_same_rules(self) -> None:
        """실발행 판과 next/ 판의 노션 은퇴 핵심 문장이 같다(한쪽만 고치면 리허설과 실발행이 갈린다)."""
        for f, phrases in (("RUN.md", ["**Notion 은 발행 길에 없다**", "GRM_INTAKE_NOTION=off"]),
                           ("copyedit.md", ["**Notion 마감은 없다.**", "3-3. **2차 마감 전 다음 할 일"])):
            live = ROOT.joinpath("company", "editorial", f).read_text(encoding="utf-8")
            nxt = ROOT.joinpath("company", "editorial", "next", f).read_text(encoding="utf-8")
            for p in phrases:
                with self.subTest(f=f, p=p):
                    self.assertIn(p, live)
                    self.assertIn(p, nxt)


if __name__ == "__main__":
    unittest.main()
