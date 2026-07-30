"""OCR 엔진 배선 표류 가드 — 483 PDF 경로를 쓰는 워크플로 전수 스캔.

2026-07-30 실장애: 스캔 483 OCR 을 도입한 PR #456 은 `collect_fda_483` 에 OCR 경로를 넣고
tesseract 설치 스텝을 **`grm-intake.yml` 에만** 추가했다. 그런데 같은 함수
(`_fetch_fda483_pdf_text`)를 호출하는 진입점이 그때 이미 넷이었다:

  · collect_intake.py            → grm-intake.yml                    (설치됨 ✔)
  · fda483_ocr_backfill.py       → grm-fda483-ocr-backfill.yml       (설치됨 ✔)
  · collect_fda_backfill.py      → grm-findings-backfill-fetch.yml   (누락 ✘ 하루 3회 무인)
  · verify_published_sources.py  → grm-source-verification.yml       (누락 ✘ 주간)

누락 둘은 각기 다른 방식으로 조용했다:
  · 백필은 스캔 483 을 `scan-ocr-unavailable` 로 **빈 본문 적재**했다(실측 31건). raw_signals
    는 append-only + document_id dedup 이라 그 문서는 영구히 빈손이 된다.
  · 원문 재대조는 text="" → found=0 이 되고, 불일치 판정이 `found > shown` 이라
    **0 은 결코 불일치를 못 만든다** — 검증이 가장 필요한 문서가 전부 'ok' 로 통과했다.

두 경우 다 워크플로는 초록이었다. 그래서 이 가드는 "설치 스텝이 있는지"를 사람이 세는 대신
**호출 그래프에서 유도**한다. 허용목록 손열거를 쓰지 않는다 — 이 저장소는 손열거가 낡아
침묵 구멍이 난 전례가 셋이다(웹 테스트 shim `__all__` / CI `py_compile` 손열거 / 이 사건).
스캔 결과가 0건이면 실패로 처리한다(빈 결과가 성공으로 읽히는 함정 방지).
"""
from __future__ import annotations

import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_DIR = os.path.join(REPO, ".github", "workflows")

# 이 액션이 tesseract 설치·검증의 **단일 정의**다. 경로가 바뀌면 이 상수만 고친다.
OCR_ACTION_REF = "./.github/actions/setup-ocr"

# 483 PDF 텍스트/OCR 경로로 들어가는 호출. 이름 하나라도 소스에 있으면 그 모듈은
# 러너에 OCR 엔진을 요구한다(전부 최종적으로 `_ocr_483_pdf_text` 로 수렴한다).
OCR_CALL_RE = re.compile(
    r"_fetch_fda483_pdf_text\s*\(|_ocr_483_pdf_text\s*\(|collect_fda_483\s*\(")

# 워크플로 셸 본문의 `python <script>.py` 호출(모듈이 루트 평면이라 경로가 없다).
WORKFLOW_PY_RE = re.compile(r"python3?\s+([a-z0-9_]+)\.py")


def _root_modules_requiring_ocr() -> set[str]:
    """루트 `*.py` 중 483 PDF/OCR 경로를 직접 호출하는 모듈 이름 집합."""
    found: set[str] = set()
    for name in os.listdir(REPO):
        if not name.endswith(".py"):
            continue
        path = os.path.join(REPO, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            if OCR_CALL_RE.search(fh.read()):
                found.add(name[:-3])
    return found


def _workflow_files() -> list[str]:
    return sorted(
        os.path.join(WORKFLOW_DIR, n)
        for n in os.listdir(WORKFLOW_DIR)
        if n.endswith((".yml", ".yaml"))
    )


class OcrEngineWiringTest(unittest.TestCase):

    def test_scan_finds_ocr_callers(self) -> None:
        """스캔이 0건이면 정규식이 낡은 것이다 — 빈 결과를 성공으로 읽지 않는다."""
        modules = _root_modules_requiring_ocr()
        self.assertGreaterEqual(len(modules), 4,
                                f"483 OCR 호출 모듈 스캔이 비었거나 너무 적다: {sorted(modules)}")
        # 정의 모듈 자신은 반드시 잡힌다(정규식 생존 확인).
        self.assertIn("collect_fda_483", modules)

    def test_scan_finds_workflows(self) -> None:
        self.assertGreater(len(_workflow_files()), 0, "워크플로 디렉터리 스캔이 비었다")

    def test_ocr_action_exists(self) -> None:
        """단일 정의가 실제로 존재한다 — 워크플로가 없는 액션을 가리키면 런타임에만 터진다."""
        action = os.path.join(REPO, ".github", "actions", "setup-ocr", "action.yml")
        self.assertTrue(os.path.isfile(action), f"복합 액션이 없다: {action}")

    def test_every_workflow_running_483_pdf_path_installs_ocr(self) -> None:
        """483 PDF 경로 스크립트를 실행하는 워크플로는 전부 OCR 액션을 부른다.

        이 테스트가 이 PR 의 본체다 — 같은 결함(호출지 하나만 고치고 나머지는 뒤처짐)이
        재발하면 CI 에서 잡힌다.
        """
        ocr_modules = _root_modules_requiring_ocr()
        offenders: list[str] = []
        checked = 0
        for path in _workflow_files():
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
            invoked = set(WORKFLOW_PY_RE.findall(body))
            needs = sorted(invoked & ocr_modules)
            if not needs:
                continue
            checked += 1
            if OCR_ACTION_REF not in body:
                offenders.append(f"{os.path.basename(path)} (실행: {', '.join(needs)})")
        self.assertGreaterEqual(checked, 4,
                                f"483 PDF 경로 워크플로 스캔이 너무 적다({checked}건) — "
                                "WORKFLOW_PY_RE 가 낡았을 수 있다")
        self.assertEqual(offenders, [],
                         "483 PDF/OCR 경로를 실행하면서 OCR 엔진을 설치하지 않는 워크플로가 있다 "
                         f"— {OCR_ACTION_REF} 를 추가하라: {offenders}")

    def test_ocr_dedicated_workflow_requires_engine(self) -> None:
        """OCR 이 존재 이유인 워크플로는 required=true 로 하드 실패해야 한다.

        수집 워크플로들과 의도적으로 다른 설정이다 — 엔진 없이 돌면 산출물이 무의미하다.
        """
        path = os.path.join(WORKFLOW_DIR, "grm-fda483-ocr-backfill.yml")
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn(OCR_ACTION_REF, body)
        self.assertRegex(body, r"required:\s*'true'")

    def test_apt_install_does_not_silence_errors(self) -> None:
        """설치 **명령줄**이 `-qq` 로 오류 출력을 죽이지 않는다(오진의 출발점이었다).

        주석 산문이 아니라 실제 `apt-get` 줄만 본다 — 해설에 `-qq` 를 인용하는 것은 정상이다.
        """
        action = os.path.join(REPO, ".github", "actions", "setup-ocr", "action.yml")
        with open(action, encoding="utf-8") as fh:
            body = fh.read()
        apt_lines = [ln for ln in body.splitlines()
                     if "apt-get" in ln and not ln.lstrip().startswith("#")]
        self.assertTrue(apt_lines, "apt-get 설치 줄을 못 찾았다 — 액션이 바뀌었나")
        quiet = [ln.strip() for ln in apt_lines if "-qq" in ln]
        self.assertEqual(quiet, [],
                         "apt-get -qq 는 실패 사유까지 지운다 — 설치 실패가 조용해진다")
        self.assertIn("tesseract-ocr-eng", body, "eng 언어팩이 없으면 tessdata 탐색이 실패한다")


if __name__ == "__main__":
    unittest.main()
