"""MFDS 공개 보고서의 실사자 표만 읽는다. 이름은 공백·참관 표지만 분리한다.

저장된 PyMuPDF/HWPX 텍스트를 입력받는 순수 파서다. 다음 절까지 표 전체가
확인돼야 성공하며, 성명 없는 표의 반복행을 인원으로 해석하지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

PARSER_VERSION = "mfds-attendees/v1"
_HEADING = re.compile(r"실(?:태조사|사)자")
_BOUNDARY = re.compile(r"(?:\d+[.)]?)?실태조사결과")
_HEADER = re.compile(r"(?:소속|기관)부서(성명)?")
_AGENCY = re.compile(
    r"식품의약품안전처|식약처|(?:서울|경인|대전|대구|광주|부산)(?:지방)?"
    r"식품의약품(?:안전|실사)청|(?:서울|경인|대전|대구|광주|부산)지방식약청"
)
_DEPARTMENT = re.compile(r"[가-힣]+?(?:과|단T/F)")
_NAME = re.compile(r"[가-힣]{2,5}")
_LEAD = ":：❍○ㅇ□■●◦•-涫"


@dataclass(frozen=True)
class Attendee:
    inspector_name_norm: str | None
    agency: str
    dept: str
    is_observer: bool | None


@dataclass(frozen=True)
class AttendeeTable:
    table_status: str
    attendees: tuple[Attendee, ...] = ()
    reason: str = ""
    table_count: int = 0

    def payload(self) -> dict:
        return {
            "table_status": self.table_status,
            "reason": self.reason,
            "parser_version": PARSER_VERSION,
            "table_count": self.table_count,
            "attendees": [asdict(row) for row in self.attendees],
        }


def parse_attendee_table(text: str | None) -> AttendeeTable:
    """표 전체와 다음 절 경계가 확인된 경우만 성공한다. I/O는 하지 않는다."""
    if not isinstance(text, str) or not text.strip():
        return AttendeeTable("failed", reason="missing_text")
    compact = re.sub(r"\s+", "", text)
    headings = list(_HEADING.finditer(compact))
    if not headings:
        return AttendeeTable("absent")
    attendees: list[Attendee] = []
    named = False
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(compact)
        result = _parse_one_table(compact[heading.end():end])
        if result.table_status == "failed":
            return AttendeeTable("failed", reason=f"table_{index + 1}:{result.reason}",
                                 table_count=len(headings))
        named = named or result.table_status == "named"
        attendees.extend(result.attendees)
    # 같은 표가 반복된 문서는 동일 행을 한 번 보존한다. 기관·부서 또는 참관 여부가
    # 다른 행은 원문 사실로 남긴다. 개인별 문서 수는 별도로 이름×문서를 DISTINCT한다.
    return AttendeeTable("named" if named else "named_none",
                         tuple(dict.fromkeys(attendees)), table_count=len(headings))


def document_inspector_names(result: AttendeeTable, *, include_observers: bool = False) -> frozenset[str]:
    """동일 이름은 표·부서가 여러 개여도 한 문서에서 한 번만 센다."""
    return frozenset(a.inspector_name_norm for a in result.attendees
                     if a.inspector_name_norm and (include_observers or a.is_observer is False))


def _parse_one_table(tail: str) -> AttendeeTable:
    boundary = _BOUNDARY.search(tail)
    if boundary is None:
        return AttendeeTable("failed", reason="missing_section_boundary")
    table = tail[:boundary.start()].lstrip(_LEAD)
    header = _HEADER.match(table)
    if header is None:
        return AttendeeTable("failed", reason="unrecognized_columns")
    named = header[1] is not None
    body = table[header.end():]
    agencies = list(_AGENCY.finditer(body))
    if not agencies or agencies[0].start() != 0:
        return AttendeeTable("failed", reason="unrecognized_agency")
    attendees = []
    for index, agency in enumerate(agencies):
        end = agencies[index + 1].start() if index + 1 < len(agencies) else len(body)
        cell = body[agency.end():end]
        department = _DEPARTMENT.match(cell)
        if department is None:
            return AttendeeTable("failed", reason="unrecognized_department")
        name = cell[department.end():]
        observer = name.endswith("(참관)")
        if observer:
            name = name[:-4]
        if (named and not _NAME.fullmatch(name)) or (not named and name):
            return AttendeeTable("failed", reason="incomplete_row")
        attendees.append(Attendee(name if named else None, agency[0], department[0],
                                  observer if named else None))
    return AttendeeTable("named" if named else "named_none", tuple(attendees))
