"""Shared literal date and publication-role rules; no inference or file access.

This module deliberately does not infer an event year from a publication date.
The same functions are used by input cards and the deterministic checker.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Mapping

PUBLICATION_ROLE = "publication_time_not_event_time"
PUBLICATION_QUESTION_ROLES = frozenset({
    "publication", "event_publication_comparison", "reporting_chronology",
})

_FULL = re.compile(
    r"(?<![\d.])(?:(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日|"
    r"(\d{4})([-/.])(\d{1,2})\5(\d{1,2}))(?!\d)"
)
_MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})月\s*(\d{1,2})日")
_YEAR = re.compile(r"(?<!\d)(\d{4})年")
_BARE_MD = re.compile(r"(?<![A-Za-z0-9_.\-/])(\d{1,2})([./-])(\d{1,2})(?![\d.\-/])")
# A bare decimal is not a date. Require an explicit nearby temporal cue, and
# reject measurement/money/version suffixes before applying that cue.
_NON_DATE_SUFFIX = re.compile(r"\s*(?:[%％‰]|亿元|万元|元|亿|万|吨|毫米|厘米|米|倍|万元|kW|MW|mm|cm|kg|[A-Za-z])", re.I)
_TIME_BEFORE = re.compile(r"(?:日期[：:]?|定于|将于|于|截至|截止|时间[：:]?)\s*$")
_TIME_AFTER = re.compile(r"\s*(?:日|号|上午|下午|晚间|当天|正式上线|正式发布|开工|投产|举行|上线|发布|交付|签约)")


def parse_date_value(value: Any, precision: str) -> str | None:
    """Validate and normalize one *declared* date, without adding precision."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        if precision == "full_date":
            m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", value)
            if not m:
                m = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", value)
            if m:
                return dt.date(*map(int, m.groups())).isoformat()
        elif precision == "month_day":
            m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})", value)
            if not m:
                m = re.fullmatch(r"(\d{1,2})月(\d{1,2})日", value)
            if m:
                month, day = map(int, m.groups())
                # Year absent: Feb 29 is possible; no particular year is asserted.
                dt.date(2000, month, day)
                return f"{month:02d}-{day:02d}"
        elif precision == "year_month":
            m = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})月?", value)
            if m:
                year, month = map(int, m.groups())
                dt.date(year, month, 1)
                return f"{year:04d}-{month:02d}"
        elif precision == "year":
            m = re.fullmatch(r"(\d{4})年?", value)
            if m and 1 <= int(m[1]) <= 9999:
                return f"{int(m[1]):04d}"
    except (ValueError, OverflowError):
        return None
    return None


def extract_literal_dates(text: str) -> list[list[str]]:
    """Return literal date values, rejecting amounts and invalid calendar dates.

    A valid full date also supports its literal year and month/day components.
    An invalid full date is not salvaged as a partial month/day assertion.
    """
    if not isinstance(text, str):
        return []
    found: list[list[str]] = []
    full_spans: list[tuple[int, int]] = []

    def add(precision: str, value: str | None) -> None:
        if value is not None and [precision, value] not in found:
            found.append([precision, value])

    def in_full(start: int, end: int) -> bool:
        return any(lo <= start and end <= hi for lo, hi in full_spans)

    for match in _FULL.finditer(text):
        full_spans.append(match.span())
        if match[1]:
            year, month, day = map(int, (match[1], match[2], match[3]))
        else:
            year, month, day = map(int, (match[4], match[6], match[7]))
        value = parse_date_value(f"{year:04d}-{month:02d}-{day:02d}", "full_date")
        if value:
            add("full_date", value)
            add("year", f"{year:04d}")
            add("month_day", f"{month:02d}-{day:02d}")
    for match in _YEAR.finditer(text):
        if not in_full(*match.span()):
            add("year", parse_date_value(match[1], "year"))
    for match in _MONTH_DAY.finditer(text):
        if not in_full(*match.span()):
            add("month_day", parse_date_value(f"{match[1]}-{match[2]}", "month_day"))
    for match in _BARE_MD.finditer(text):
        if in_full(*match.span()):
            continue
        before = text[max(0, match.start() - 12):match.start()]
        after = text[match.end():match.end() + 14]
        if _NON_DATE_SUFFIX.match(after):
            continue
        if not (_TIME_BEFORE.search(before) or _TIME_AFTER.match(after)):
            continue
        add("month_day", parse_date_value(f"{match[1]}-{match[3]}", "month_day"))
    return found


def classify_question_date_role(question: str) -> str:
    """Choose whether publication metadata is needed; never a semantic verdict."""
    q = str(question)
    publication = bool(re.search(
        r"发表(?:日|时间|日期|于)|发布(?:日|时间|日期|于)|刊发(?:日|时间|日期|于)|"
        r"报道(?:日|时间|日期)|发文(?:日|时间|日期)|何时(?:发表|刊发|发文)|"
        r"(?:文章|报道|材料|新闻).{0,8}(?:哪天|何时|什么时候).{0,5}(?:发布|发表|刊发)", q))
    chronology = bool(re.search(r"(?:报道|文章|材料|新闻).{0,16}(?:先后|早于|晚于|较早|较晚|更早|更晚|最早|最晚|时间顺序|(?:先|后)(?:发表|发布|刊发))", q))
    if chronology:
        return "reporting_chronology"
    if publication and re.search(r"事件|计划|投产|开工|签约|交付|上线|是否相同|是不是同一天|区分|比较", q):
        return "event_publication_comparison"
    return "publication" if publication else "event_or_other"


def _publication_date(raw: str) -> tuple[str, str] | None:
    # Publication timestamps can include an explicitly provided time/zone.
    m = re.fullmatch(
        r"\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})"
        r"(?:[ T](\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?\s*", raw)
    if m:
        value = parse_date_value(f"{m[1]}-{m[2]}-{m[3]}", "full_date")
        if value is None:
            return None
        if m[4] is not None and (int(m[4]) > 23 or int(m[5]) > 59 or int(m[6] or 0) > 59):
            return None
        return value, "full_date"
    for precision in ("full_date", "year_month", "year"):
        value = parse_date_value(raw, precision)
        if value:
            return value, precision
    return None


def normalize_publication_metadata(
    metadata: Mapping[str, Any] | None, sources: Mapping[str, Any] | list[str] | tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    """Normalize only supplied source metadata, tolerating the two bundle shapes.

    Missing or invalid metadata is absent, never inferred. The raw value and
    source ID remain available so a publication answer can cite its actual role.
    """
    if not isinstance(metadata, Mapping):
        return {}
    while isinstance(metadata.get("source_metadata"), Mapping):
        metadata = metadata["source_metadata"]
    out: dict[str, dict[str, Any]] = {}
    for sid in sources:
        item = metadata.get(sid)
        if not isinstance(item, Mapping) or item.get("model_generated") is True:
            continue
        raw = item.get("pub", item.get("published_at"))
        if not isinstance(raw, str):
            continue
        parsed = _publication_date(raw)
        if not parsed:
            continue
        value, precision = parsed
        out[sid] = {
            "published_at": raw, "value": value, "precision": precision,
            "role": PUBLICATION_ROLE, "origin": "frozen_input_source_metadata",
            "model_generated": False,
        }
    return out
