#!/usr/bin/env python3
"""루트 CLI 스크립트는 좁은 콘솔 인코딩에서도 출력이 죽지 않아야 한다.

## 왜 이 검사가 있나

2026-08-19 실측: `findings_facets_refresh.py` 가 Windows 로컬(cp949)에서 제외 항목 요약의
**em-dash 한 글자** 때문에 `UnicodeEncodeError` 로 죽었다. 파일 쓰기가 그 로그 다음이라
Supabase RPC 90여 회(수 분)를 다 돌고도 산출물을 하나도 남기지 못했다(EXIT=1).

핵심은 **ubuntu CI 가 UTF-8 이라 이 결함이 영원히 초록**이라는 것이다. 이 저장소는 한국어
산출물을 다루므로 로그에 비ASCII 가 항상 섞이고, Supabase 에서 읽어온 규제 원문에도
em-dash·불릿·따옴표가 그대로 들어 있다. 그래서 "리터럴만 조심하면 된다"가 성립하지 않는다.

## 왜 손목록이 아니라 파생인가

이 관용구는 이미 저장소에 있었다(`brief_lint.py`·`deep_analysis_fanout.py`·`probe_*.py`).
그런데 **손으로 하나씩 복사한 가드라 새 스크립트에는 안 붙었다** — 사고를 낸 파일의
독스트링은 자기가 `glossary_cases_refresh.py` 와 "같은 구조"라고 적어두고 있었고, 정작 그
파일에는 가드가 있고 이 파일에는 없었다. `probe_source_reachability.py` 도 형제
`probe_datago.py`·`probe_oglmpp.py`·`probe_recall.py` 셋만 가드를 갖고 저 혼자 빠져 있었다.

그래서 이 검사는 **대상을 파일시스템에서 파생**한다(제외 목록 없음). 새 CLI 스크립트를
추가하면 자동으로 검사 범위에 들어온다 — 그게 이 검사의 존재 이유다.

## 검사 대상 판정

`if __name__ == "__main__":` 블록이 있으면 CLI 진입점으로 본다. 그 블록이 함수 호출 한
문장뿐이면(`main()`·`sys.exit(main())`·`raise SystemExit(main())`) 그 함수가 진입점이고,
여러 문장짜리면 블록 자체가 진입점이다. 진입점 안(또는 모듈 수준)에 가드가 있어야 한다.

라이브러리 함수 안에 가드를 넣는 것은 **오답**이다 — 그 함수를 import 해 쓰는
오케스트레이터의 stdout 까지 조용히 갈아끼우게 된다.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MAIN_TEST = '__name__ == "__main__"'

# 이 저장소가 인정하는 두 관용구.
#   1) stream.reconfigure(encoding="utf-8", ...)        — 현행
#   2) sys.stdout = io.TextIOWrapper(..., encoding="utf-8")  — 종전(eudragmdp_client 등)
GUIDANCE = (
    '진입점 첫머리에 다음을 넣을 것:\n'
    '    for stream in (sys.stdout, sys.stderr):\n'
    '        try:\n'
    '            stream.reconfigure(encoding="utf-8", errors="replace")\n'
    '        except (AttributeError, ValueError):\n'
    '            pass'
)


def _main_block(tree: ast.Module) -> "ast.If | None":
    for node in tree.body:
        if (isinstance(node, ast.If)
                and ast.unparse(node.test).replace("'", '"') == MAIN_TEST):
            return node
    return None


def _entry(tree: ast.Module, blk: ast.If):
    """(진입점 노드, 표시용 이름). 블록이 단일 호출이면 그 함수, 아니면 블록."""
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    if len(blk.body) == 1:
        for call in ast.walk(blk.body[0]):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                fn = funcs.get(call.func.id)
                if fn is not None:
                    return fn, f"{fn.name}()"
    return blk, '__main__ 블록'


def _guards(node) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                and sub.func.attr == "reconfigure":
            for kw in sub.keywords:
                if kw.arg == "encoding" and isinstance(kw.value, ast.Constant) \
                        and str(kw.value.value).lower().replace("-", "") == "utf8":
                    return True
        if isinstance(sub, ast.Assign) and "TextIOWrapper" in ast.unparse(sub.value):
            if any(ast.unparse(t) in ("sys.stdout", "sys.stderr") for t in sub.targets):
                return True
    return False


# 루트 평면(코드 배치 원칙)과 `web/` 의 CLI 넷(render·linkcheck·announce·newsletter).
# `web/tests/` 는 비재귀 글롭이라 들어오지 않는다 — 테스트는 사람이 직접 돌리는 CLI 가
# 아니고, 러너가 인코딩을 정한다.
SCAN_GLOBS = ("*.py", "web/*.py")


def _cli_scripts():
    """CLI 스크립트 전수 — 손목록이 아니라 파일시스템에서 파생한다."""
    out = []
    for pattern in SCAN_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):    # pragma: no cover
                continue
            blk = _main_block(tree)
            if blk is not None:
                out.append((path, tree, blk))
    return out


class CliStdoutEncodingGuardTest(unittest.TestCase):
    def test_derivation_is_not_vacuous(self):
        """대상이 0개면 검사 자체가 무력하다 — 글롭이 깨지면 조용히 초록이 된다."""
        scripts = _cli_scripts()
        self.assertGreater(len(scripts), 50,
                           f"CLI 스크립트를 {len(scripts)}개밖에 못 찾았다 — 파생이 깨졌다")

    def test_every_cli_script_survives_a_narrow_console_encoding(self):
        missing = []
        for path, tree, blk in _cli_scripts():
            entry, where = _entry(tree, blk)
            # 진입점 안이 정석이지만, 모듈 수준에 둔 것도 효과는 같으므로 인정한다.
            if not (_guards(entry) or _guards(tree)):
                missing.append(f"{path.relative_to(ROOT).as_posix()} ({where})")
        self.assertEqual(
            missing, [],
            f"stdout 인코딩 가드가 없는 CLI 스크립트 {len(missing)}개:\n"
            + "\n".join("  · " + m for m in missing)
            + "\n\n" + GUIDANCE)


class NarrowEncodingFactsTest(unittest.TestCase):
    """가드가 막는 대상을 못박는다 — "한글이 되니까 괜찮다" 는 오해를 차단."""

    def test_cp949_encodes_hangul_but_not_em_dash(self):
        for ok in "한글·→★①":
            ok.encode("cp949")                          # 예외 없음 = 통과
        for bad in "—•✓⚠":
            with self.assertRaises(UnicodeEncodeError,
                                   msg=f"{bad!r} 가 cp949 로 인코딩됐다"):
                bad.encode("cp949")


if __name__ == "__main__":                                       # pragma: no cover
    unittest.main()
