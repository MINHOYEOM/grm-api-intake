"""워크플로 ENABLE_* 플래그 해석 관용구 표류 가드 — `.github/workflows/*` 전수 스캔.

2026-07-20 실장애: `grm-intake.yml` 의 옛 관용구
    ${{ (github.event_name == 'workflow_dispatch') && format('{0}', inputs.x) || vars.X || 'false' }}
는 `format('{0}', false)` = 문자열 `"false"` = **truthy** 라 거기서 short-circuit 되어,
**입력을 아무것도 주지 않은 dispatch 가 repo 변수(vars)로 켜 둔 기능을 조용히 껐다**
(ENABLE_FDA_483_OBSERVATIONS 가 false 로 뒤집혀 483 관찰 추출이 통째로 빠짐).

2026-07-13 에 handoff v2 계열만 쓰던 대안
    ${{ (github.event_name == 'workflow_dispatch' && inputs.x) && 'true' || vars.X || 'false' }}
은 "미지정=상속"은 지키지만 **명시적 false 를 vars 로 덮는** 반대 결함이 있다.

정본은 3-상태 입력(`inherit`/`true`/`false`)을 쓰는 다음 형태다:
    ${{ (github.event_name == 'workflow_dispatch' && inputs.x != 'inherit') && inputs.x || vars.X || '<기본>' }}

**허용목록 손열거를 쓰지 않는다** — 이 저장소는 손열거가 낡아 침묵 구멍이 난 전례가 2건
있다(웹 테스트 shim `__all__` 67건 미실행 / CI `py_compile` 루트 모듈 36개 미게이트).
워크플로 디렉터리를 전수 스캔하고, **스캔 결과가 0건이면 실패**로 처리한다(빈 결과가
성공으로 읽히는 함정 방지).
"""
from __future__ import annotations

import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_DIR = os.path.join(REPO, ".github", "workflows")

# `ENABLE_X: ${{ ... }}` 형태의 env 배정 한 줄
FLAG_LINE = re.compile(r"^\s*(?P<flag>ENABLE_[A-Z0-9_]+):\s*(?P<expr>\$\{\{.*\}\})\s*$")

# 정본 — 3-상태 해석
SAFE_INPUT = re.compile(
    r"^\$\{\{\s*\(\s*github\.event_name == 'workflow_dispatch'\s*&&\s*"
    r"inputs\.(?P<a>\w+)\s*!=\s*'inherit'\s*\)\s*&&\s*inputs\.(?P<b>\w+)\s*"
    r"\|\|\s*vars\.(?P<var>\w+)\s*\|\|\s*'(?P<default>[^']*)'\s*\}\}$")

# 입력을 안 쓰는 vars 전용(안전 — 덮어쓸 입력 자체가 없다)
SAFE_VARS_ONLY = re.compile(r"^\$\{\{\s*vars\.(?P<var>\w+)\s*\|\|\s*'(?P<default>[^']*)'\s*\}\}$")

# 알려진 결함 관용구 — 발견 즉시 실패(사유를 명확히 알려준다)
BANNED = [
    (re.compile(r"format\('\{0\}',\s*inputs\."),
     "format('{0}', inputs.*) — boolean false 가 문자열 \"false\"(truthy)가 되어 "
     "입력 미지정 dispatch 가 vars 를 덮는다(2026-07-20 실장애)"),
    (re.compile(r"&&\s*inputs\.\w+\s*\)\s*&&\s*'true'"),
     "(… && inputs.x) && 'true' — 명시적 false 를 vars 가 덮는다(2026-07-13 관용구)"),
]

MAX_DISPATCH_INPUTS = 25   # GitHub workflow_dispatch inputs 상한(실측)


def _declared_dispatch_inputs(path: str) -> dict[str, dict[str, object]]:
    """`on.workflow_dispatch.inputs` 를 **표준 라이브러리만으로** 파싱한다.

    PyYAML 을 쓰지 않는 이유: CI 테스트 환경에 PyYAML 이 없다(requirements.txt 미포함 —
    2026-07-20 실측으로 확인). 표류 가드가 선택적 의존성 때문에 죽으면 가드가 아니다.
    필요한 것은 입력 이름·type·default·options·개수뿐이라 들여쓰기 기반 파싱으로 충분하다.
    (`tests/test_workflow_flag_resolution.py` 자체 테스트가 PyYAML 결과와 대조해 정합성을
     확인한다 — 로컬처럼 PyYAML 이 있는 환경에서만 그 대조가 돌고, 없으면 건너뛴다.)
    """
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    def indent(s: str) -> int:
        return len(s) - len(s.lstrip(" "))

    # 1) `workflow_dispatch:` 찾기 → 그 아래 `inputs:` 찾기
    i, n = 0, len(lines)
    inputs_indent = None
    while i < n:
        line = lines[i]
        if line.strip().startswith("workflow_dispatch:"):
            wd_indent = indent(line)
            j = i + 1
            while j < n:
                cur = lines[j]
                if cur.strip() and indent(cur) <= wd_indent:
                    break                       # workflow_dispatch 블록 종료
                if cur.strip() == "inputs:":
                    inputs_indent = indent(cur)
                    i = j
                    break
                j += 1
            break
        i += 1
    if inputs_indent is None:
        return {}

    # 2) inputs 하위 항목 수집
    out: dict[str, dict[str, object]] = {}
    key_indent = None
    cur_key = None
    body = lines[i + 1:]
    idx = 0
    while idx < len(body):
        line = body[idx]
        idx += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        ind = indent(line)
        if ind <= inputs_indent:
            break                               # inputs 블록 종료
        if key_indent is None:
            key_indent = ind
        if ind == key_indent and line.rstrip().endswith(":"):
            cur_key = line.strip()[:-1]
            out[cur_key] = {}
        elif cur_key is not None and ind > key_indent and ":" in line:
            k, _, v = line.strip().partition(":")
            v = v.strip()
            if v == "":
                # block 시퀀스(`options:` 다음 줄부터 `- item`)를 모은다. flow 스타일
                # (`['a', 'b']`)만 처리하면 이 형태를 조용히 빈 값으로 흘려보낸다 —
                # 틀린 값을 반환하는 파서는 가드를 무력화하므로 둘 다 지원한다.
                seq, look = [], idx
                while look < len(body):
                    nxt = body[look]
                    if not nxt.strip():
                        look += 1
                        continue
                    if indent(nxt) <= ind or not nxt.lstrip().startswith("- "):
                        break
                    seq.append(_scalar(nxt.lstrip()[2:].strip()))
                    look += 1
                if seq:
                    out[cur_key][k.strip()] = seq
                    idx = look
                    continue
            out[cur_key][k.strip()] = _scalar(v)
    return out


def _scalar(raw: str) -> object:
    """YAML 스칼라 최소 해석 — 따옴표 제거·bool·리스트(flow) 처리."""
    if raw.startswith("[") and raw.endswith("]"):
        body = raw[1:-1].strip()
        if not body:
            return []
        return [_scalar(x.strip()) for x in body.split(",")]
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        return raw[1:-1]
    if raw in ("true", "false"):
        return raw == "true"
    return raw


def _workflow_files() -> list[str]:
    if not os.path.isdir(WORKFLOW_DIR):
        return []
    return sorted(os.path.join(WORKFLOW_DIR, f) for f in os.listdir(WORKFLOW_DIR)
                  if f.endswith((".yml", ".yaml")))


def _flag_lines() -> list[tuple[str, int, str, str]]:
    """(파일명, 줄번호, 플래그명, 표현식) 전수."""
    out = []
    for path in _workflow_files():
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                m = FLAG_LINE.match(line.rstrip("\n"))
                if m:
                    out.append((os.path.basename(path), i, m["flag"], m["expr"]))
    return out


class ScanSanityTest(unittest.TestCase):
    """스캔 자체가 살아있는지 — 0건은 성공이 아니라 실패다."""

    def test_workflow_dir_found(self):
        self.assertTrue(_workflow_files(), f"워크플로 파일 0개 — 경로 확인: {WORKFLOW_DIR}")

    def test_flag_lines_found(self):
        lines = _flag_lines()
        self.assertGreater(len(lines), 0,
                           "ENABLE_* 해석 라인 0건 — 정규식이 낡았거나 스캔 경로가 틀렸다. "
                           "빈 결과를 통과로 읽으면 가드가 침묵한다.")


class BannedIdiomTest(unittest.TestCase):
    """알려진 결함 관용구가 어디에도 없어야 한다(주석 포함 — 주석이 남으면 다시 퍼진다)."""

    def test_no_banned_idiom_in_any_workflow(self):
        hits = []
        for path in _workflow_files():
            with open(path, encoding="utf-8") as fh:
                for i, line in enumerate(fh, 1):
                    for pat, why in BANNED:
                        if pat.search(line) and not line.lstrip().startswith("#"):
                            hits.append(f"{os.path.basename(path)}:{i} — {why}")
        self.assertEqual(hits, [], "결함 관용구 발견:\n  " + "\n  ".join(hits))


class FlagResolutionPatternTest(unittest.TestCase):
    """모든 ENABLE_* 해석 라인이 안전 패턴 둘 중 하나여야 한다."""

    def test_every_flag_line_is_safe(self):
        bad = []
        for fname, lineno, flag, expr in _flag_lines():
            if SAFE_INPUT.match(expr) or SAFE_VARS_ONLY.match(expr):
                continue
            bad.append(f"{fname}:{lineno} {flag} = {expr}")
        self.assertEqual(bad, [],
                         "안전 패턴이 아닌 플래그 해석:\n  " + "\n  ".join(bad) +
                         "\n허용 형태 = 3-상태 입력 해석 또는 vars 전용")

    def test_input_and_vars_names_are_consistent(self):
        """같은 줄 안에서 두 번 참조하는 input 이름이 일치해야 한다(복붙 사고 가드)."""
        bad = []
        for fname, lineno, flag, expr in _flag_lines():
            m = SAFE_INPUT.match(expr)
            if m and m["a"] != m["b"]:
                bad.append(f"{fname}:{lineno} {flag} — inputs.{m['a']} vs inputs.{m['b']}")
        self.assertEqual(bad, [], "입력 이름 불일치:\n  " + "\n  ".join(bad))


class ThreeStateInputDeclarationTest(unittest.TestCase):
    """3-상태 해석을 쓰는 플래그는 그 입력이 실제로 3-상태로 선언돼 있어야 한다."""

    def _declared_inputs(self, path: str) -> dict[str, dict]:
        return _declared_dispatch_inputs(path)

    def test_referenced_inputs_are_choice_with_inherit_default(self):
        bad = []
        for path in _workflow_files():
            declared = self._declared_inputs(path)
            with open(path, encoding="utf-8") as fh:
                for i, line in enumerate(fh, 1):
                    m = FLAG_LINE.match(line.rstrip("\n"))
                    if not m:
                        continue
                    sm = SAFE_INPUT.match(m["expr"])
                    if not sm:
                        continue
                    name = sm["a"]
                    spec = declared.get(name)
                    fn = os.path.basename(path)
                    if spec is None:
                        bad.append(f"{fn}:{i} inputs.{name} 미선언")
                        continue
                    if spec.get("type") != "choice":
                        bad.append(f"{fn}:{i} inputs.{name} type={spec.get('type')} (choice 여야 함)")
                    if spec.get("default") != "inherit":
                        bad.append(f"{fn}:{i} inputs.{name} default={spec.get('default')!r} "
                                   f"('inherit' 여야 미지정=상속이 성립)")
                    opts = spec.get("options") or []
                    if sorted(opts) != ["false", "inherit", "true"]:
                        bad.append(f"{fn}:{i} inputs.{name} options={opts}")
        self.assertEqual(bad, [], "3-상태 입력 선언 위반:\n  " + "\n  ".join(bad))


class DispatchInputCapTest(unittest.TestCase):
    """workflow_dispatch 입력 상한(25) — 넘으면 dispatch 자체가 422 로 거부된다."""

    def test_no_workflow_exceeds_input_cap(self):
        over = []
        for path in _workflow_files():
            ins = _declared_dispatch_inputs(path)
            if len(ins) > MAX_DISPATCH_INPUTS:
                over.append(f"{os.path.basename(path)}: {len(ins)}개 > {MAX_DISPATCH_INPUTS}")
        self.assertEqual(over, [], "입력 상한 초과:\n  " + "\n  ".join(over))


class StdlibParserAgreesWithPyYamlTest(unittest.TestCase):
    """자체 파서가 PyYAML 과 같은 결과를 내는지 — PyYAML 이 있는 환경에서만 돈다.

    CI 는 PyYAML 이 없어 자체 파서로만 검사한다(그래서 파서가 틀리면 가드 전체가 조용히
    무력해진다). 개발 환경에는 PyYAML 이 있으므로 여기서 정합성을 붙잡아 둔다.
    """

    def test_parser_matches_pyyaml(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML 없음(CI) — 자체 파서 단독 동작")
        mismatches = []
        for path in _workflow_files():
            with open(path, encoding="utf-8") as fh:
                doc = yaml.safe_load(fh)
            on = doc.get(True) or doc.get("on") or {}
            wd = on.get("workflow_dispatch") if isinstance(on, dict) else None
            ref = (wd or {}).get("inputs") or {} if isinstance(wd, dict) else {}
            mine = _declared_dispatch_inputs(path)
            fn = os.path.basename(path)
            if set(ref) != set(mine):
                mismatches.append(f"{fn}: 키 불일치 {sorted(set(ref) ^ set(mine))}")
                continue
            for k in ref:
                for field in ("type", "default", "options", "required"):
                    if field in ref[k] and ref[k][field] != mine[k].get(field):
                        mismatches.append(
                            f"{fn}:{k}.{field} pyyaml={ref[k][field]!r} 자체={mine[k].get(field)!r}")
        self.assertEqual(mismatches, [], "자체 파서 ≠ PyYAML:\n  " + "\n  ".join(mismatches))


class ActionsExpressionQuotingTest(unittest.TestCase):
    """`${{ }}` 안의 큰따옴표는 HTTP 422(dispatch 거부) — YAML 검사로는 안 잡힌다.

    2026-07-20 실측: `|| \"n/a\"` 가 워크플로 파싱 단계에서 거부돼 기동 자체가 막혔다.
    """

    EXPR = re.compile(r"\$\{\{(?P<body>[^}]*)\}\}")

    def test_no_double_quotes_inside_expressions(self):
        bad = []
        for path in _workflow_files():
            with open(path, encoding="utf-8") as fh:
                for i, line in enumerate(fh, 1):
                    for m in self.EXPR.finditer(line):
                        if '"' in m["body"]:
                            bad.append(f"{os.path.basename(path)}:{i} {m.group(0)[:70]}")
        self.assertEqual(bad, [], "표현식 내 큰따옴표(422 유발):\n  " + "\n  ".join(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
