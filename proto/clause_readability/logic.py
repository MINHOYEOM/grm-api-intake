"""Pure reference logic for the offline clause readability demo."""

import re
from functools import lru_cache


_ASCII_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_WHITESPACE = "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_HANGUL = re.compile(r"[가-힣]")
_LATIN_TOKEN = re.compile(r"[A-Za-z0-9/-]+")
_PRE_VERBISH = re.compile(r"(?:기|을|를)\s$")
_POST_VERBISH = re.compile(r"^(?:하|되|한|된|할|합|됩)")


def normalize_query(query: str) -> str:
    return query.strip(_WHITESPACE).translate(_ASCII_FOLD)


def find_spans(text: str, query: str) -> list[tuple[int, int]]:
    """Return literal overlapping matches as half-open character offsets."""
    query = normalize_query(query)
    if not query:
        return []
    folded = text.translate(_ASCII_FOLD)
    spans = []
    start = folded.find(query)
    while start >= 0:
        spans.append((start, start + len(query)))
        start = folded.find(query, start + 1)
    return spans


def step_result(current: int, total: int, direction: int) -> int:
    """Return the next zero-based result, or -1 when there are no results."""
    if not total:
        return -1
    if current == -1:
        return 0 if direction == 1 else total - 1
    return (current + direction) % total


@lru_cache(maxsize=512)
def _term_pattern(surface: str, lang: str) -> re.Pattern[str]:
    literal = re.escape(surface)
    if lang == "en" or _LATIN_TOKEN.fullmatch(surface):
        flags = re.IGNORECASE if lang == "en" else 0
        return re.compile(rf"(?<![A-Za-z0-9]){literal}(?![A-Za-z0-9])", flags)
    if not _HANGUL.search(surface):
        return re.compile(literal)

    # The renderer permits one space or NBSP at eligible Hangul boundaries.
    words = re.findall(r"\S+", surface)
    tight = "".join(words)
    spaces = set()
    offset = 0
    for word in words[:-1]:
        offset += len(word)
        spaces.add(offset)
    total = len(_HANGUL.findall(tight))
    left = 0
    parts = []
    flexible = False
    for offset, char in enumerate(tight):
        if offset and _HANGUL.fullmatch(tight[offset - 1]) and _HANGUL.fullmatch(char) and (
            offset in spaces or (left >= 2 and total - left >= 2)
        ):
            parts.append(r"[ \u00a0]?")
            flexible = True
        elif offset in spaces:
            parts.append(" ")
        parts.append(re.escape(char))
        left += bool(_HANGUL.fullmatch(char))
    return re.compile("".join(parts) if flexible else literal)


def term_spans(
    text: str,
    selected: list[tuple[str, str]],
    lang: str = "ko",
    used: set[str] | None = None,
) -> list[tuple[int, int, str]]:
    """Apply renderer-style selection priority, without emitting HTML."""
    used = set() if used is None else used
    spans = []
    for surface, term_id in selected:
        if not surface or term_id in used:
            continue
        check_hangul = lang == "ko" and bool(_HANGUL.search(surface))
        check_verbish = lang == "ko" and not _LATIN_TOKEN.fullmatch(surface)
        pattern = _term_pattern(surface, lang)
        match = pattern.search(text)
        while match:
            start, end = match.span()
            inside_word = check_hangul and start > 0 and _HANGUL.fullmatch(text[start - 1])
            verbish = check_verbish and (
                _PRE_VERBISH.search(text[max(0, start - 2):start])
                or _POST_VERBISH.match(text[end:end + 2])
            )
            overlaps = any(left < end and start < right for left, right, _ in spans)
            if not (inside_word or verbish or overlaps):
                spans.append((start, end, term_id))
                used.add(term_id)
                break
            match = pattern.search(text, start + 1)
    return sorted(spans)
