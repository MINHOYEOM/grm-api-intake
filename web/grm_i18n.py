"""GRM 웹 다국어(i18n) — 문구 사전·추출기·검사기 (2026-09-03 다국어 2단계).

설계(설계 문서 docs/specs/GRM_다국어_영문판_설계_2026-09-03.md §3·§7):
- **키 = 한국어 원문 그대로**(gettext 방식). 키를 새로 짓지 않으므로 템플릿·JS·파이썬의
  문장은 그 자리에서 `_("…")`·`_t("…")`·`tr("…")` 로 감싸기만 한다. 한국어 빌드는 항등이라
  산출물이 바이트 단위로 불변이다(리팩터 증명 = 전 파일 md5 대조).
- 영어 카탈로그 `web/data/i18n/en.json` = {한국어 키: 영어}. **키 결손은 en 빌드에서 즉시
  실패**한다 — 영어 페이지에 한국어가 조용히 남는 것을 막는다(이 저장소의 "조용한 0장
  금지" 규율과 같은 결).
- 치환 슬롯은 세 층 공통 `{name}` 문법(`_("문서 {n}건", n=x)`). 템플릿 층은 값을 escape
  한다(종전 `{{ x }}` 와 같은 결과). 슬롯 집합은 키와 번역이 같아야 한다(검사기가 본다).
- 카탈로그 정합은 **추출기가 소스에서 키를 읽어** 검사한다(손목록 없음): 템플릿은 Jinja
  AST, JS 는 `_t("…")` 리터럴, 파이썬은 `tr("…")`/`N_("…")` AST. 감싸지 않은 한글이 남으면
  검사기가 파일:줄 을 대며 실패한다. 데이터 비교값(`it.state == '신규'`)처럼 화면 문구가
  아닌 리터럴만 `i18n-ignore` 마커로 면제한다(마커 남용은 코드 리뷰가 본다).
- Admin 콘솔(admin.html·admin.js)은 운영자 전용·한국어 고정이라 대상이 아니다.

CLI:
  python web/grm_i18n.py lint            # 감싸지 않은 한글 + 카탈로그 결손/고아/슬롯 불일치
  python web/grm_i18n.py keys            # 소스에서 추출한 키 전량(JSON 배열)
  python web/grm_i18n.py missing [lang]  # 카탈로그에 없는 키(번역 배치 입력)
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

from jinja2 import Environment
from markupsafe import Markup, escape

WEB_DIR = Path(__file__).resolve().parent
I18N_DIR = WEB_DIR / "data" / "i18n"
TEMPLATES_DIR = WEB_DIR / "templates"
PARTIALS_DIR = WEB_DIR / "partials"
ASSETS_DIR = WEB_DIR / "assets"
RENDER_PY = WEB_DIR / "render.py"

DEFAULT_LANG = "ko"
SUPPORTED_LANGS = ("ko", "en")

# 운영자 전용 화면 — 한국어 고정(영어 트리에 실리지 않는다).
EXCLUDED_TEMPLATES = frozenset({"admin.html"})
EXCLUDED_ASSETS = frozenset({"admin.js"})

IGNORE_MARK = "i18n-ignore"
TEMPLATE_FUNCS = ("_",)
JS_FUNC = "_t"
PY_FUNCS = ("tr", "N_")

SLOT_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
HANGUL_RE = re.compile(r"[\u3131-\u318E\uAC00-\uD7A3]")


class MissingTranslation(KeyError):
    """카탈로그에 없는 키 — en 빌드는 여기서 멈춘다(한국어 잔존 금지)."""


# ── 카탈로그 ──────────────────────────────────────────────────────────────────
def catalog_path(lang: str, i18n_dir: Path = I18N_DIR) -> Path:
    return i18n_dir / f"{lang}.json"


def load_catalog(lang: str, i18n_dir: Path = I18N_DIR) -> dict[str, str]:
    """언어 카탈로그 로드. 기본 언어(ko)는 항등이라 빈 사전. 형식이 어긋나면 즉시 실패."""
    if lang == DEFAULT_LANG:
        return {}
    if lang not in SUPPORTED_LANGS:
        raise ValueError(f"모르는 언어 코드: {lang!r} (허용: {SUPPORTED_LANGS})")
    path = catalog_path(lang, i18n_dir)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise ValueError(f"카탈로그가 비었거나 dict 가 아니다: {path}")
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError(f"카탈로그 항목은 문자열→문자열이어야 한다: {k!r}")
    return data


def slots_of(text: str) -> frozenset[str]:
    return frozenset(SLOT_RE.findall(text))


def fill(text: str, slots: dict[str, Any],
         convert: Callable[[Any], str] = str) -> str:
    """`{name}` 슬롯 치환. 없는 이름은 KeyError(조용히 `{name}` 을 남기지 않는다)."""
    def _sub(m: "re.Match[str]") -> str:
        name = m.group(1)
        if name not in slots:
            raise KeyError(f"치환 슬롯 값 없음: {{{name}}} in {text!r}")
        return convert(slots[name])
    return SLOT_RE.sub(_sub, text)


class Translator:
    """언어 하나에 묶인 번역 함수. `tr("문서 {n}건", n=5)` → 문자열(escape 없음)."""

    __slots__ = ("lang", "catalog")

    def __init__(self, lang: str = DEFAULT_LANG,
                 catalog: "dict[str, str] | None" = None) -> None:
        if lang not in SUPPORTED_LANGS:
            raise ValueError(f"모르는 언어 코드: {lang!r}")
        self.lang = lang
        self.catalog = {} if lang == DEFAULT_LANG else (
            catalog if catalog is not None else load_catalog(lang))

    def lookup(self, text: str) -> str:
        # 빈 문자열은 언어와 무관하게 빈 문자열 — 선택 필드(link_label 등)가 비어 있을 때
        # "" 를 카탈로그에 등록하라고 요구하지 않는다.
        if self.lang == DEFAULT_LANG or not text:
            return text
        try:
            return self.catalog[text]
        except KeyError:
            raise MissingTranslation(f"[{self.lang}] 번역 없음: {text!r}") from None

    def __call__(self, text: str, **slots: Any) -> str:
        # 슬롯 치환은 **항상** 돈다 — 슬롯이 있는 키를 값 없이 부르면 `{n}` 이 화면에
        # 그대로 남는 대신 KeyError 로 멈춘다(조용한 결손 금지).
        return fill(self.lookup(text), slots)

    def template_gettext(self) -> Callable[..., Markup]:
        """템플릿 전역 `_` — 값은 escape, 문구 자체는 Markup(종전 템플릿 원문과 바이트 동일)."""
        def _(text: str, **slots: Any) -> Markup:
            return Markup(fill(self.lookup(text), slots,
                               convert=lambda v: str(escape(v))))
        return _


def noop(text: str) -> str:
    """`N_("…")` — 모듈 상수를 키로 표시만 한다(번역은 쓰는 자리에서 `tr(상수)`)."""
    return text


# 기본 언어 번역기(항등) — 헬퍼 함수의 `tr=` 기본값. 테스트가 헬퍼를 직접 부르면 한국어.
KO = Translator(DEFAULT_LANG)


# ── JS 층 ────────────────────────────────────────────────────────────────────
# 각 자산 파일 머리에 그대로 들어가는 shim. 사전(window.GRM_I18N)은 영어 페이지에서만
# 실리고, 없으면 항등이라 한국어 페이지의 동작은 종전과 같다. 슬롯 치환은 사전 유무와
# 무관하게 돌아야 하므로(`_t("{n}건", {n: 5})` 은 한국어에서도 치환이 필요) shim 이 직접 한다.
# 파일마다 사본을 두는 이유: 공유 스크립트 하나에 의존하면 그 로드 실패가 전 페이지의
# 문구를 `{n}건` 그대로 노출시킨다. 사본은 검사기가 바이트 동일을 강제한다.
JS_SHIM = (
    "  var _t = function (s, v) {\n"
    "    var d = window.GRM_I18N, r = (d && Object.prototype.hasOwnProperty.call(d, s)) ? d[s] : s;\n"
    "    return v ? r.replace(/\\{(\\w+)\\}/g, function (m, k) {\n"
    "      return Object.prototype.hasOwnProperty.call(v, k) ? String(v[k]) : m; }) : r;\n"
    "  };\n"
)


def build_js_catalog(catalog: dict[str, str], keys: Iterable[str]) -> str:
    """영어 페이지에 실을 사전 스크립트 — JS 가 실제로 쓰는 키만, 정렬·결정론."""
    subset = {k: catalog[k] for k in sorted(set(keys))}
    return ("window.GRM_I18N=" + json.dumps(subset, ensure_ascii=False, sort_keys=True,
                                              separators=(",", ":")) + ";\n")


# ── 추출 ──────────────────────────────────────────────────────────────────────
def _template_env() -> Environment:
    # 추출 전용 — 렌더 환경과 같은 블록 옵션이어야 AST 줄 번호가 일치한다.
    return Environment(trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)


def template_files(web_dir: Path = WEB_DIR) -> list[Path]:
    files = sorted((web_dir / "templates").glob("*.html")) + \
        sorted((web_dir / "partials").glob("*.html"))
    return [p for p in files if p.name not in EXCLUDED_TEMPLATES]


def asset_files(web_dir: Path = WEB_DIR) -> list[Path]:
    return [p for p in sorted((web_dir / "assets").glob("*.js"))
            if p.name not in EXCLUDED_ASSETS]


def _template_calls(path: Path):
    """템플릿 AST 의 `_()` 호출 노드 — 추출기·검사기 공용."""
    from jinja2 import nodes  # 지역 import — 추출기 밖에서는 필요 없다
    src = path.read_text(encoding="utf-8")
    tree = _template_env().parse(src, name=path.name, filename=str(path))
    for node in tree.find_all(nodes.Call):
        if isinstance(node.node, nodes.Name) and node.node.name in TEMPLATE_FUNCS:
            yield node


def iter_template_keys(path: Path) -> list[tuple[int, str]]:
    """`_("…")` 의 리터럴 키. 계약: 위치 인자는 문자열 리터럴 하나, 슬롯 값은 키워드로만."""
    from jinja2 import nodes
    out: list[tuple[int, str]] = []
    for node in _template_calls(path):
        if len(node.args) != 1 or node.dyn_args is not None:
            raise ValueError(f"{path.name}:{node.lineno} — `_()` 위치 인자는 하나뿐이어야 한다")
        first = node.args[0]
        if isinstance(first, nodes.Const) and isinstance(first.value, str):
            out.append((node.lineno, first.value))
        # 리터럴이 아닌 첫 인자(`_(it.state)`)는 데이터 키 — N_ 로 등록된 키를 쓴다.
    return out


def find_template_slot_mismatch(path: Path) -> list[tuple[int, str]]:
    """`_("…{n}…")` 의 슬롯과 키워드 인자가 어긋난 호출 — (줄, 설명).

    빌드 시점의 KeyError(`치환 슬롯 값 없음`)를 감싸는 단계에서 잡는다. 슬롯이 있는데
    키워드가 없거나 이름이 다르면 화면에 `{n}` 이 남거나 빌드가 죽는다.
    """
    from jinja2 import nodes
    found: list[tuple[int, str]] = []
    for node in _template_calls(path):
        if not node.args or not isinstance(node.args[0], nodes.Const):
            continue
        key = node.args[0].value
        if not isinstance(key, str):
            continue
        need = slots_of(key)
        given = {kw.key for kw in node.kwargs}
        if node.dyn_kwargs is not None:
            continue                       # `**kw` 는 정적으로 판정 불가
        if need - given:
            found.append((node.lineno, f"슬롯 값 없음 {sorted(need - given)}: {key[:60]!r}"))
        if given - need:
            found.append((node.lineno, f"키에 없는 키워드 {sorted(given - need)}: {key[:60]!r}"))
    return found


# JS 문자열 리터럴 스캐너 — 주석·정규식 리터럴을 건너뛰고 문자열 토큰만 낸다.
_JS_REGEX_PREV = frozenset("(,=:[!&|?{};+-*%<>~^")
_JS_KEYWORD_BEFORE_REGEX = ("return", "typeof", "case", "do", "else", "in", "of",
                            "instanceof", "new", "delete", "void", "throw")


def scan_js_strings(src: str) -> list[tuple[int, int, str, str]]:
    """(start, end, quote, raw_body) 목록. 템플릿 리터럴은 quote='`'."""
    out: list[tuple[int, int, str, str]] = []
    i, n = 0, len(src)
    last_sig = ""            # 직전 의미 문자(공백·주석 제외) — 정규식/나눗셈 판별용
    last_word = ""
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "\"'`":
            j = i + 1
            while j < n:
                ch = src[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == c:
                    break
                if c != "`" and ch == "\n":
                    break                      # 닫히지 않은 문자열 — 줄 끝에서 멈춘다
                j += 1
            out.append((i, j + 1, c, src[i + 1:j]))
            i = j + 1
            last_sig, last_word = c, ""
            continue
        if c == "/":
            is_regex = (last_sig == "" or last_sig in _JS_REGEX_PREV
                        or last_word in _JS_KEYWORD_BEFORE_REGEX)
            if is_regex:
                j, in_class = i + 1, False
                while j < n:
                    ch = src[j]
                    if ch == "\\":
                        j += 2
                        continue
                    if ch == "[":
                        in_class = True
                    elif ch == "]":
                        in_class = False
                    elif ch == "/" and not in_class:
                        break
                    elif ch == "\n":
                        break
                    j += 1
                i = j + 1
                last_sig, last_word = ")", ""
                continue
        if c.isalnum() or c == "_" or c == "$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            last_word = src[i:j]
            last_sig = "w"
            i = j
            continue
        last_sig, last_word = c, ""
        i += 1
    return out


_JS_ALLOWED_ESCAPES = re.compile(r"\\(?![\"\\])")


def _js_unescape(body: str) -> str:
    return body.replace('\\"', '"').replace("\\\\", "\\")


def _line_of(src: str, pos: int) -> int:
    return src.count("\n", 0, pos) + 1


def _ignored_lines(src: str) -> set[int]:
    return {i + 1 for i, ln in enumerate(src.splitlines()) if IGNORE_MARK in ln}


def iter_js_keys(path: Path) -> list[tuple[int, str]]:
    """`_t("…")` 의 키. 계약: 큰따옴표 리터럴 하나, 이스케이프는 `\\"`·`\\\\` 만."""
    src = path.read_text(encoding="utf-8")
    out: list[tuple[int, str]] = []
    for start, end, quote, body in scan_js_strings(src):
        head = src[:start].rstrip()
        if not head.endswith(JS_FUNC + "("):
            continue
        line = _line_of(src, start)
        if quote != '"':
            raise ValueError(f"{path.name}:{line} — `{JS_FUNC}()` 키는 큰따옴표 리터럴이어야 한다")
        if _JS_ALLOWED_ESCAPES.search(body):
            raise ValueError(f"{path.name}:{line} — `{JS_FUNC}()` 키에 허용되지 않은 이스케이프: {body!r}")
        out.append((line, _js_unescape(body)))
    return out


def iter_py_keys(path: Path = RENDER_PY) -> list[tuple[int, str]]:
    """`tr("…")`·`N_("…")` 의 리터럴 키(AST). 첫 인자가 리터럴이 아니면 무시한다(`tr(상수)`)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else "")
        if name not in PY_FUNCS:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            out.append((node.lineno, first.value))
    return out


def collect_keys(web_dir: Path = WEB_DIR) -> dict[str, list[str]]:
    """키 → 쓰인 자리(`file:line`) 목록. 템플릿·JS·render.py 전수."""
    keys: dict[str, list[str]] = {}

    def add(key: str, where: str) -> None:
        keys.setdefault(key, []).append(where)

    for p in template_files(web_dir):
        for line, key in iter_template_keys(p):
            add(key, f"{p.name}:{line}")
    for p in asset_files(web_dir):
        for line, key in iter_js_keys(p):
            add(key, f"{p.name}:{line}")
    for line, key in iter_py_keys(web_dir / "render.py"):
        add(key, f"render.py:{line}")
    return keys


def js_keys(web_dir: Path = WEB_DIR) -> set[str]:
    """JS 가 쓰는 키만(영어 페이지에 실을 사전의 범위)."""
    out: set[str] = set()
    for p in asset_files(web_dir):
        out.update(k for _, k in iter_js_keys(p))
    return out


# ── 검사 ──────────────────────────────────────────────────────────────────────
_JINJA_COMMENT_RE = re.compile(r"\{#.*?#\}", re.S)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_JINJA_TAG_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)


def _blank_keep_lines(text: str) -> str:
    """구간을 지우되 줄 수는 보존한다(줄 번호 보고용)."""
    return "".join(ch if ch == "\n" else " " for ch in text)


_SCRIPT_BODY_RE = re.compile(r"(<script\b[^>]*>)(.*?)(</script>)", re.S | re.I)
_STYLE_BODY_RE = re.compile(r"(<style\b[^>]*>)(.*?)(</style>)", re.S | re.I)
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)


def find_bare_hangul_template(path: Path) -> list[tuple[int, str]]:
    """`_()` 밖에 남은 한글 — (줄, 발췌). 주석은 제외, `i18n-ignore` 줄은 면제.

    순서: Jinja/HTML 주석 → Jinja 태그(한글이 있는데 `_(` 가 없으면 보고) → 인라인
    `<script>` 본문은 JS 스캐너로(주석 제외·문자열 리터럴만) → `<style>` 의 CSS 주석 제거 →
    남은 텍스트·속성값의 한글 전부 보고.
    """
    src = path.read_text(encoding="utf-8")
    ignored = _ignored_lines(src)
    work = _JINJA_COMMENT_RE.sub(lambda m: _blank_keep_lines(m.group(0)), src)
    work = _HTML_COMMENT_RE.sub(lambda m: _blank_keep_lines(m.group(0)), work)
    found: list[tuple[int, str]] = []

    def _tag(m: "re.Match[str]") -> str:
        body = m.group(0)
        if HANGUL_RE.search(body) and "_(" not in body:
            found.append((_line_of(work, m.start()), body.strip()[:80]))
        return _blank_keep_lines(body)
    work = _JINJA_TAG_RE.sub(_tag, work)

    def _script(m: "re.Match[str]") -> str:
        body = m.group(2)
        base = m.start(2)
        for start, _end, quote, s_body in scan_js_strings(body):
            if HANGUL_RE.search(s_body):
                found.append((_line_of(work, base + start), (quote + s_body)[:80]))
        return m.group(1) + _blank_keep_lines(body) + m.group(3)
    work = _SCRIPT_BODY_RE.sub(_script, work)

    def _style(m: "re.Match[str]") -> str:
        body = _CSS_COMMENT_RE.sub(lambda c: _blank_keep_lines(c.group(0)), m.group(2))
        return m.group(1) + body + m.group(3)
    work = _STYLE_BODY_RE.sub(_style, work)

    for i, ln in enumerate(work.splitlines(), start=1):
        m = HANGUL_RE.search(ln)
        if m:
            found.append((i, ln.strip()[max(0, m.start() - 30):][:80]))
    return sorted({(l, s) for l, s in found if l not in ignored})


def find_bare_hangul_js(path: Path) -> list[tuple[int, str]]:
    """`_t()` 밖의 한글 문자열 리터럴(템플릿 리터럴 포함) + 비리터럴 `_t(` 호출."""
    src = path.read_text(encoding="utf-8")
    ignored = _ignored_lines(src)
    found: list[tuple[int, str]] = []
    for start, end, quote, body in scan_js_strings(src):
        line = _line_of(src, start)
        if line in ignored:
            continue
        head = src[:start].rstrip()
        wrapped = head.endswith(JS_FUNC + "(")
        if HANGUL_RE.search(body) and not wrapped:
            found.append((line, (quote + body)[:80]))
    # `_t(` 뒤에 문자열 리터럴이 아닌 것이 오면 키를 추출할 수 없다.
    for m in re.finditer(re.escape(JS_FUNC) + r"\(\s*(?![\"'`])", src):
        line = _line_of(src, m.start())
        if line not in ignored and not src[m.end():m.end() + 1] == ")":
            found.append((line, src[m.start():m.start() + 60].replace("\n", " ")))
    # 슬롯이 있는 키인데 둘째 인자(값 객체)가 없으면 `{n}` 이 화면에 그대로 남는다.
    for start, end, quote, body in scan_js_strings(src):
        if not src[:start].rstrip().endswith(JS_FUNC + "(") or quote != '"':
            continue
        if slots_of(_js_unescape(body)) and not src[end:end + 40].lstrip().startswith(","):
            line = _line_of(src, start)
            if line not in ignored:
                found.append((line, f"슬롯 값 객체 없음: {_js_unescape(body)[:60]!r}"))
    return sorted(set(found))


def check_js_shim(path: Path) -> "str | None":
    src = path.read_text(encoding="utf-8")
    return None if JS_SHIM in src else f"{path.name}: JS shim(JS_SHIM) 사본 없음/불일치"


def check_catalog(catalog: dict[str, str], keys: dict[str, list[str]],
                  lang: str = "en") -> list[str]:
    """결손·고아·슬롯 불일치·빈 값·미번역(한글 잔존)을 문장으로 낸다."""
    problems: list[str] = []
    for key in sorted(keys):
        if key not in catalog:
            problems.append(f"[{lang}] 결손: {key!r} ← {', '.join(keys[key][:3])}")
    for key in sorted(catalog):
        if key not in keys:
            problems.append(f"[{lang}] 고아(소스에 없음): {key!r}")
    for key, val in sorted(catalog.items()):
        if not val.strip():
            problems.append(f"[{lang}] 빈 번역: {key!r}")
        elif HANGUL_RE.search(val):
            problems.append(f"[{lang}] 번역에 한글 잔존: {key!r} → {val!r}")
        # 번역은 키의 슬롯 **일부만** 써도 된다(영어에 조사가 없듯 값을 버릴 수 있다).
        # 키에 없는 슬롯을 쓰는 것은 결손 — 렌더 시 KeyError 로 죽기 전에 여기서 잡는다.
        extra = slots_of(val) - slots_of(key)
        if extra:
            problems.append(f"[{lang}] 키에 없는 슬롯 {sorted(extra)}: {key!r} → {val!r}")
    return problems


def lint(web_dir: Path = WEB_DIR, langs: Iterable[str] = ("en",),
         require_catalog: bool = True) -> list[str]:
    problems: list[str] = []
    for p in template_files(web_dir):
        for line, snip in find_bare_hangul_template(p):
            problems.append(f"감싸지 않은 한글 {p.name}:{line}: {snip}")
        for line, snip in find_template_slot_mismatch(p):
            problems.append(f"슬롯 불일치 {p.name}:{line}: {snip}")
    for p in asset_files(web_dir):
        for line, snip in find_bare_hangul_js(p):
            problems.append(f"감싸지 않은 한글 {p.name}:{line}: {snip}")
        shim = check_js_shim(p)
        if shim:
            problems.append(shim)
    keys = collect_keys(web_dir)
    for lang in langs:
        path = catalog_path(lang, web_dir / "data" / "i18n")
        if not path.is_file():
            if require_catalog:
                problems.append(f"[{lang}] 카탈로그 파일 없음: {path}")
            continue
        problems.extend(check_catalog(load_catalog(lang, web_dir / "data" / "i18n"),
                                      keys, lang))
    return problems


def main(argv: "list[str] | None" = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "lint"
    if cmd == "lint":
        problems = lint()
        for p in problems:
            print(p)
        print(f"i18n lint: {len(problems)} problem(s)")
        return 1 if problems else 0
    if cmd == "keys":
        keys = collect_keys()
        print(json.dumps([{"key": k, "where": v} for k, v in sorted(keys.items())],
                         ensure_ascii=False, indent=1))
        return 0
    if cmd == "missing":
        lang = args[1] if len(args) > 1 else "en"
        path = catalog_path(lang)
        catalog = load_catalog(lang) if path.is_file() else {}
        keys = collect_keys()
        missing = [k for k in sorted(keys) if k not in catalog]
        print(json.dumps(missing, ensure_ascii=False, indent=1))
        print(f"missing: {len(missing)} / keys: {len(keys)}", file=sys.stderr)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
