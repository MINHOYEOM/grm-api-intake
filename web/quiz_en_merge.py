"""[주간 퀴즈] 영문 문항 병합기 — 번역 결과를 정본 뱅크에 **가산**한다(2026-09-05).

    python web/quiz_en_merge.py --check                       # 정본 그대로 게이트만
    python web/quiz_en_merge.py trans1.json trans2.json       # 미리보기(쓰지 않음)
    python web/quiz_en_merge.py trans*.json --apply           # 정본에 반영

입력은 `[{id, question_en, choices_en, explanation_en}, …]` 배열(여러 파일 허용).

설계 — 이 스크립트가 지키는 것:
- **가산만 한다.** 기존 키(`question_ko`·`choices`·`answer_index`·…)는 값도 순서도 건드리지
  않는다. 영문 세 필드는 항상 **같은 자리**(기존 키 뒤)에 같은 순서로 붙어, 두 번 돌려도
  같은 바이트가 나온다(결정론 — 골든 대조가 의미를 가지려면 필요하다).
- **셋을 전부 채우거나 전부 비운다.** 하나라도 빠진 항목은 병합을 **거부**한다. 반쪽 영어를
  데이터에 남기면 렌더가 조용히 그 문항만 빼고, 사람은 뺀 줄 모른다(브리프의 반쪽 영어
  금지와 같은 규율 — README 불변식 #9).
- **`choices_en` 은 `choices` 와 같은 길이·같은 순서.** `answer_index` 는 두 언어가 공유하는
  하나의 값이라, 순서가 바뀌면 화면은 멀쩡한데 **정답만 조용히 어긋난다**. 길이는 여기서
  세고, 순서는 사람이 지킬 수밖에 없으므로 정답 선택지의 **의미가 자리를 지켰는지**를
  사람이 볼 수 있게 미리보기에 정답 줄을 나란히 찍는다.
- **EN_INVENTED_NUMBER.** 영문이 한국어 원문에 없는 수치를 말하면 거부한다
  (`render.validate_quiz_en_facts` — 브리프의 `validate_brief_en_facts` 와 같은 검사).
  방향은 비대칭이다: 덜 말하는 것은 되고 **없는 것을 말하는 것**만 막는다.

★검사는 렌더와 **같은 함수**를 부른다(사본 0). 병합기만 아는 규칙을 따로 두면 그 규칙이
  낡는 순간 빌드와 갈라진다 — 이 저장소가 반복해서 데인 자리다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import render  # noqa: E402  (경로 주입 후 import — web/ 를 패키지로 만들지 않는 저장소 관례)

QUIZ_FILE = render.QUIZ_FILE


def _detect_indent(path: Path, default: int = 2) -> int:
    """정본 JSON 의 들여쓰기 폭을 파일에서 읽는다(배열 첫 원소 줄의 선행 공백).

    포맷을 바꾸지 않기 위한 최소 장치다 — 못 읽으면 이 파일의 현행값(2)으로 떨어진다.
    """
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        stripped = line.lstrip(" ")
        if stripped:
            return len(line) - len(stripped) or default
    return default


def load_translations(paths: list[Path]) -> dict[str, dict[str, Any]]:
    """번역 파일 여러 개 → {id: {세 필드}}. 같은 id 가 두 번 오면 즉시 실패한다
    (나중 것이 조용히 이기면 어느 파일이 반영됐는지 아무도 모른다)."""
    out: dict[str, dict[str, Any]] = {}
    for p in paths:
        items = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            raise ValueError(f"번역 파일은 배열이어야 한다: {p}")
        for it in items:
            qid = str(it.get("id") or "")
            if not qid:
                raise ValueError(f"{p}: id 없는 항목")
            if qid in out:
                raise ValueError(f"{p}: id 중복 — {qid}")
            out[qid] = {k: it[k] for k in render.QUIZ_EN_FIELDS if k in it}
    return out


def merge(bank: list[dict[str, Any]],
          trans: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """뱅크 + 번역 → (새 뱅크, 문제 목록). 문제가 하나라도 있으면 호출부가 쓰지 않는다."""
    problems: list[str] = []
    by_id = {str(q.get("id") or ""): q for q in bank}
    for qid in sorted(set(trans) - set(by_id)):
        problems.append(f"{qid}: 뱅크에 없는 id")

    merged: list[dict[str, Any]] = []
    for q in bank:
        qid = str(q.get("id") or "")
        new = dict(q)                     # 기존 키·순서 보존(파이썬 dict 는 삽입순서)
        en = trans.get(qid)
        if en:
            present = [f for f in render.QUIZ_EN_FIELDS if en.get(f)]
            if len(present) != len(render.QUIZ_EN_FIELDS):
                missing = [f for f in render.QUIZ_EN_FIELDS if not en.get(f)]
                problems.append(f"{qid}: EN_PARTIAL — 빠진 필드 {missing}")
            elif len(en["choices_en"]) != len(q.get("choices") or []):
                problems.append(
                    f"{qid}: EN_CHOICES_LEN — choices {len(q.get('choices') or [])}개 "
                    f"vs choices_en {len(en['choices_en'])}개 "
                    f"(answer_index={q.get('answer_index')} 가 어긋난다)")
            else:
                # 항상 같은 자리·같은 순서로 붙인다 → 두 번 돌려도 byte 동일.
                for f in render.QUIZ_EN_FIELDS:
                    new[f] = en[f]
        merged.append(new)

    problems.extend(render.validate_quiz_en_facts(merged))
    return merged, problems


def preview(bank: list[dict[str, Any]], merged: list[dict[str, Any]]) -> str:
    """사람이 볼 미리보기 — **정답 선택지를 두 언어로 나란히** 찍는다. 순서가 바뀌었는지는
    기계가 볼 수 없고(둘 다 문자열 4개), 사람이 이 두 줄을 보면 바로 안다."""
    lines: list[str] = []
    added = 0
    for old, new in zip(bank, merged):
        if any(f in new and f not in old for f in render.QUIZ_EN_FIELDS):
            added += 1
            ai = new.get("answer_index")
            lines.append(f"+ {new.get('id')}  answer_index={ai}")
            lines.append(f"    ko: {(new.get('choices') or [''])[ai]}")
            lines.append(f"    en: {(new.get('choices_en') or [''])[ai]}")
    lines.append(f"— 영문 가산 {added}건 / 뱅크 {len(merged)}문항, "
                 f"영어로 낼 수 있는 문항 "
                 f"{sum(1 for q in merged if render.quiz_has_english(q))}건")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    # 좁은 콘솔 인코딩(Windows cp949 등)에서 출력이 죽지 않게 한다 — cp949 는 한글은
    # 찍어도 em-dash/불릿 같은 글자를 못 찍어 UnicodeEncodeError 로 죽는다. ubuntu CI 는
    # UTF-8 이라 이 결함이 초록으로 숨는다. quiz_lint.py 등과 동형
    # (`tests/test_cli_stdout_encoding.py` 가 전 CLI 진입점에 이 블록을 강제한다).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="주간 퀴즈 영문 문항 병합기")
    ap.add_argument("translations", nargs="*", type=Path,
                    help="번역 JSON 파일(배열). 여러 개 허용")
    ap.add_argument("--bank", type=Path, default=QUIZ_FILE, help="정본 뱅크 경로")
    ap.add_argument("--apply", action="store_true", help="정본에 실제로 쓴다")
    ap.add_argument("--check", action="store_true",
                    help="번역 없이 정본만 게이트에 건다(CI 용)")
    args = ap.parse_args(argv)

    bank = json.loads(args.bank.read_text(encoding="utf-8"))
    trans = load_translations(args.translations) if args.translations else {}
    merged, problems = merge(bank, trans)

    if problems:
        print(f"거부 — {len(problems)}건:")
        for p in problems:
            print(f"  · {p}")
        return 1

    print(preview(bank, merged))
    if args.check:
        return 0
    if not args.apply:
        print("(미리보기 — 쓰려면 --apply)")
        return 0

    # ★들여쓰기는 **원본에서 읽는다**. 저장소의 다른 data 파일은 indent=1 이지만 이 뱅크는
    #   indent=2 다 — 관례를 믿고 새로 찍으면 3필드 가산이 945줄 전면 재작성으로 보여
    #   리뷰에서 진짜 변경이 묻힌다(실제로 한 번 그렇게 나왔다). 병합기는 포맷을 바꾸지
    #   않는다: 값만 더하고 모양은 있던 그대로 돌려준다.
    render._write(args.bank,
                  json.dumps(merged, ensure_ascii=False,
                             indent=_detect_indent(args.bank)) + "\n")
    print(f"기록: {args.bank}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
