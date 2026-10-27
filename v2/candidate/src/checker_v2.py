"""RAG 2 semantic diagnostics, offline and read-only with respect to answers.

The checker separates structure, source binding, narrative, and gold review.
No status-to-polarity inference, publication-to-event-year inference, or
structure-only whole-answer trust is permitted. These deterministic checks
are deliberately limited; no finding is not a proof of semantic correctness.
"""

from __future__ import annotations

import re
import json
import pathlib
import importlib.util
import sys
from datetime import date
from decimal import Decimal
from dataclasses import dataclass, field, asdict
from typing import Any

SCHEMA = "RAG2_checker_v2_1"

# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------

BLOCKING = "blocking"
ADVISORY = "advisory"

LOCUS_CANONICAL = "canonical_object"
LOCUS_NARRATIVE = "narrative_answer"
LOCUS_GOLD = "gold_label"


@dataclass
class Finding:
    code: str
    locus: str
    severity: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Verdict:
    case_id: str
    blocking: list[Finding] = field(default_factory=list)
    advisory: list[Finding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.blocking

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "whole_answer_trust": "review_required" if self.passed else "blocked",
            "confirmed": False,
            "passed_scope": "limited_deterministic_checks_only",
            "blocking": [f.as_dict() for f in self.blocking],
            "advisory": [f.as_dict() for f in self.advisory],
        }


# --------------------------------------------------------------------------
# date handling
# --------------------------------------------------------------------------

# One shared calendar parser is also used by build_inputs/evidence_cards.
def _shared_module(name: str):
    if name in sys.modules:
        return sys.modules[name]
    path = pathlib.Path(__file__).resolve().parents[2] / "production" / "src" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"bundled dependency unavailable: {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def _dates(text: str) -> list[list[str]]:
    return _shared_module("date_roles").extract_literal_dates(text)


def _quote_limit() -> int | None:
    """Read the contractual quote length from the single schema source.

    Length is a contract fact, not a checker constant. The fallback keeps the
    diagnostic usable when the bundled production schema is unavailable.
    """
    try:
        mod = _shared_module("contract")
        return mod.SCHEMA["$defs"]["statement"]["properties"]["quote"].get("maxLength")
    except Exception:
        return None


def _schema_event_states() -> set[str]:
    """Event states currently legal in the shared schema (single source)."""
    try:
        mod = _shared_module("contract")
        return set(mod.SCHEMA["$defs"]["claim"]["properties"]["event_state"]["enum"])
    except Exception:
        return set()


# Event states the action words in a quotation can legitimately establish.
# Kept deliberately narrow: absence of an action from a verb list is never
# itself a semantic error, so mismatches only fire for a stated state that the
# cited action words actively contradict.
_ACTION_TO_STATES = {
    "开工": {"event_planned", "event_reported_completed", "event_construction_started",
             "event_conditional", "event_unknown", "event_conflicting", "event_uncertain"},
    "投产": {"event_planned", "event_reported_completed", "event_conditional",
             "event_unknown", "event_conflicting", "event_uncertain"},
    "上线": {"event_planned", "event_reported_completed", "event_conditional",
             "event_unknown", "event_conflicting", "event_uncertain"},
    "交付": {"event_planned", "event_reported_completed", "event_conditional",
             "event_unknown", "event_conflicting", "event_uncertain"},
    "签约": {"event_signed", "event_planned", "event_conditional", "event_unknown",
             "event_conflicting", "event_uncertain"},
    "收购": {"event_signed", "event_planned", "event_reported_completed", "event_conditional",
             "event_unknown", "event_conflicting", "event_uncertain"},
    "签署": {"event_signed", "event_planned", "event_conditional", "event_unknown",
             "event_conflicting", "event_uncertain"},
}
# Action words that a signing state cannot be satisfied by.
_SIGNING_ONLY_ACTIONS = {"签约", "签署"}


def _norm(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def parse_iso(value: str) -> tuple[int, int, int] | None:
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", _norm(value))
    if not m:
        return None
    try:
        d = date(*(int(x) for x in m.groups()))
    except ValueError:
        return None
    return d.year, d.month, d.day


def has_full_date_literal(text: str, ymd: tuple[int, int, int]) -> bool:
    y, m, d = ymd
    return ["full_date", f"{y:04d}-{m:02d}-{d:02d}"] in _dates(text)


def has_month_day_literal(text: str, month: int, day: int) -> bool:
    return ["month_day", f"{month:02d}-{day:02d}"] in _dates(text)


def has_year_month_literal(text: str, y: int, m: int) -> bool:
    if not 1 <= m <= 12 or not 1 <= y <= 9999:
        return False
    return bool(re.search(rf"(?<!\d){y}(?:年|[-/])0?{m}(?:月|(?!\d))", text))


def year_from_published(published_at_raw: str | None) -> int | None:
    if not published_at_raw:
        return None
    matches = [v for p, v in _dates(str(published_at_raw)) if p == "full_date"]
    return int(matches[0][:4]) if matches else None


# --------------------------------------------------------------------------
# gold normalization   (P2 + P3)
# --------------------------------------------------------------------------

# Historical status vocabularies are preserved for callers that enumerate the
# old dataset. They are NOT a map to answer_support; the original status-only
# polarity assumption was invalid for supported negative answers.
POSITIVE_STATUSES = {
    "signed",                    # Q01, Q02, Q09
    "commissioned",              # Q12
    "launched",                  # Q05
    "delivered",                 # Q04
    "online",                    # Q07
    "planned",                   # Q03  -- the plan's terms ARE stated in the report
    "signed_plan",               # Q06  -- same: figures of a signed plan are stated
    "construction_started",      # Q08  -- the start event itself is reported
    "historical",                # Q10  -- historical attribution is stated
    "reported_subsidiary",       # Q11
}

# Historical "negative" vocabulary. An answer explaining why completion is
# not established may itself be fully evidence_supported.
NEGATIVE_STATUSES = {
    "started_not_completed",     # Q14  (D17)
    "signed_not_commissioned",   # Q15  (D18)
    "target_not_result",         # Q16  (D19)
    "planned_conditional",       # Q13  (D16)
    "cross_project_insufficient",# Q20  (D21)
    "unanswerable",              # Q18, Q19
}

# The report itself is self-contradictory
CONFLICT_STATUSES = {"conflict"}  # Q17

# annotations that mark the gold author borrowed the year from publication ctx
_BORROW_MARK = "发布日上下文"
_AMBIGUOUS_MARKS = ("公告", "发布文章", "报道；", "报道", "目标；", "/")


def expected_support(status: str) -> str | None:
    """Deprecated status-only inference. A negative conclusion may be supported.

    Status describes an event, while answer_support describes support for an
    answer to a particular question. Neither positive nor negative status is
    sufficient to derive that answer-level label. Returning None is explicit
    non-adjudication, not a new gold label.
    """
    return None


def normalize_gold_time(raw: str) -> dict[str, Any]:
    """
    Parse a gold event_time string into a canonical structure.

    Returns {value, precision, provenance, caveat, parseable}
    ``parseable=False`` means the judge cannot compare it at all -- the old
    S3 number was silently dragged down by exactly these 7 labels.
    """
    s = _norm(raw)
    out: dict[str, Any] = {
        "raw": s, "value": None, "precision": "unknown",
        "provenance": None, "caveat": None, "parseable": False,
        "comparable_as_explicit_event": False,
    }
    if not s:
        return out

    # explicit publication-borrow annotation written by the gold author
    if _BORROW_MARK in s:
        base = s.split("（")[0]
        out["provenance"] = "derived_from_publication"
        out["caveat"] = "gold_author_declares_year_borrowed_from_publication"

    else:
        base = s

    ymd = parse_iso(base)
    if ymd:
        y, m, d = ymd
        out.update(
            value=f"{y:04d}-{m:02d}-{d:02d}", precision="full_date",
            parseable=True,
            comparable_as_explicit_event=out["provenance"] is None,
        )
        return out

    m_ym = re.fullmatch(r"(\d{4})-(\d{1,2})", base)
    if m_ym:
        value = _shared_module("date_roles").parse_date_value(base, "year_month")
        if value is not None:
            out.update(value=value, precision="year_month", parseable=True, comparable_as_explicit_event=out["provenance"] is None)
        else:
            out["caveat"] = "invalid_calendar_month"
        return out

    m_y = re.fullmatch(r"(\d{4})", base)
    if m_y:
        value = _shared_module("date_roles").parse_date_value(base, "year")
        if value is not None:
            out.update(value=value, precision="year", parseable=True, comparable_as_explicit_event=out["provenance"] is None)
        else:
            out["caveat"] = "invalid_calendar_year"
        return out

    # two dates concatenated, e.g. '2024/2022' -- not a single value
    if "/" in base and re.search(r"\d{4}/\d{4}", base):
        out["parseable"] = False
        out["caveat"] = "multi_date_label_not_single_value"
        return out

    # free-text role labels: '2017-06公告', '2023报道；不证明当前股权比例'
    for mark in _AMBIGUOUS_MARKS:
        if mark in base:
            out["parseable"] = False
            out["caveat"] = f"role_label_embedded:{mark}"
            # still recover the leading date if one exists
            head = re.match(r"(\d{4}[-/]\d{1,2}|\d{4})", base)
            if head:
                out["value"] = head.group(1)
                out["precision"] = "year_month" if "-" in head.group(1) else "year"
            return out

    out["caveat"] = "unrecognized_format"
    return out


def gold_status_contradiction(status: str, gold_answer: str = "") -> str | None:
    """No contradiction can be established from event status alone."""
    return None


# --------------------------------------------------------------------------
# the date provenance gate   (P3 -- the core fix)
# --------------------------------------------------------------------------


def check_date_claim(claim, statements, published_at_raw, case_id, verdict,
                     claim_id=None) -> None:
    """Validate exact event-date precision against its own cited quotations.

    Publication metadata never legitimizes an otherwise unsupported event
    year, even if the model explicitly labels the inference.
    """
    ed = claim.get("explicit_event_date") if isinstance(claim, dict) else None
    if not isinstance(ed, dict):
        verdict.blocking.append(Finding("event_date_object_invalid", LOCUS_CANONICAL,
                                        BLOCKING, f"{case_id}/{claim_id}"))
        return
    value, precision, ids = ed.get("value"), ed.get("precision"), ed.get("evidence")
    prefix = f"{case_id}/{claim_id}: "
    if precision == "unknown" and value is None:
        return
    valid_value = _shared_module("date_roles").parse_date_value(value, precision)
    if valid_value is None or not isinstance(ids, list) or not ids:
        verdict.blocking.append(Finding("explicit_event_date_not_supported_or_invalid_precision",
                                        LOCUS_CANONICAL, BLOCKING, prefix + repr(ed)))
        return
    if any(not isinstance(i, str) or i not in statements for i in ids):
        verdict.blocking.append(Finding("event_date_evidence_reference_invalid", LOCUS_CANONICAL,
                                        BLOCKING, prefix + repr(ids)))
        return
    quoted = [statements[i] for i in ids if isinstance(statements.get(i), str)]
    supported = any([precision, valid_value] in _dates(q) for q in quoted)
    if precision == "year_month":
        y, m = (int(x) for x in valid_value.split("-"))
        supported = any(has_year_month_literal(q, y, m) for q in quoted)
    if not supported:
        code = ("event_year_cannot_be_derived_from_publication"
                if ed.get("provenance") == "derived_from_publication"
                else "explicit_event_date_not_supported_or_invalid_precision")
        verdict.blocking.append(Finding(code, LOCUS_CANONICAL, BLOCKING,
                                        prefix + f"{value!r} is not in its cited evidence"))


def check_narrative_date(narrative, statements, published_at_raw, case_id, verdict) -> None:
    """The legacy narrow API has no publication role/source binding context.

    It therefore accepts only quoted full-date literals. The full production
    adapter additionally handles explicit supplied publication metadata.
    """
    if not isinstance(narrative, str):
        return
    cited_dates = {tuple(d) for q in statements.values() if isinstance(q, str) for d in _dates(q)}
    for precision, value in _dates(narrative):
        if precision != "full_date" or (precision, value) in cited_dates:
            continue
        verdict.blocking.append(Finding("narrative_full_date_without_literal_basis",
                                        LOCUS_NARRATIVE, BLOCKING,
                                        f"{case_id}: {value} absent from cited quotations"))


# --------------------------------------------------------------------------
# structured project-identity check   (S2 -- no keyword scanning)
# --------------------------------------------------------------------------

_CROSS_MARKERS = (
    "另一个", "另一", "其他", "其余", "不同项目", "不同对象",
    "不同公司", "不同的", "并非同一", "不是同一",
)


def longest_common_substring(a: str, b: str) -> int:
    """Length of the longest common substring. O(len(a)*len(b)); inputs are short."""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


# Historical retrieval-similarity constants/functions remain callable. They
# are not used as semantic identity proof by the production adapter.
GROUNDED_MIN_RUN = 4
GROUNDED_MIN_RATIO = 0.4


def grounded_ratio(name: str, text: str) -> float:
    if len(name) < 4:
        return 0.0
    return longest_common_substring(name, text) / len(name)


def check_project_identity(claims, statements, statement_sources, case_id, verdict) -> None:
    """Absence of literal identity is a review item, never LCS-based proof.

    Title/body source IDs collapse to one document. A lexical similarity score
    can help retrieval but cannot prove that two projects or companies coincide.
    """
    for c in claims if isinstance(claims, list) else []:
        if not isinstance(c, dict):
            continue
        refs = c.get("source_statement")
        if not isinstance(refs, list):
            continue
        refs = [i for i in refs if isinstance(i, str) and i in statements]
        quoted = " ".join(statements[i] for i in refs if isinstance(statements[i], str))
        names = [_norm(c.get("subject")), _norm(c.get("project_identity"))]
        sources = {_norm(statement_sources.get(i)).removesuffix("#title") for i in refs}
        if any(n and n not in quoted for n in names) or len(sources) > 1:
            verdict.advisory.append(Finding("project_identity_requires_source_context",
                                            LOCUS_CANONICAL, ADVISORY,
                                            f"{case_id}/{c.get('id')}: literal/alias identity needs source context; "
                                            "LCS and project_group are not proof"))


def narrative_hedges_cross_project(narrative: str) -> bool:
    """Does the narrative itself disclaim the cross-project identity?"""
    if not narrative:
        return False
    return any(m in narrative for m in _CROSS_MARKERS)


# --------------------------------------------------------------------------
# gold expectation gate   (P2)
# --------------------------------------------------------------------------

_OLD_EXPECT = "evidence_supported"


def check_gold_expectation(gold_status, model_support, case_id, verdict) -> None:
    """Preserve legacy gold without manufacturing a label from its status.

    Evidence-backed adjudication is an independent append-only review task.
    It is not performed by production answer validation.
    """
    verdict.advisory.append(Finding("gold_support_requires_question_and_evidence_review",
                                    LOCUS_GOLD, ADVISORY,
                                    f"{case_id}: event status {gold_status!r} alone does not determine "
                                    f"answer_support={model_support!r}; old gold unchanged"))


# --------------------------------------------------------------------------
# top-level judge
# --------------------------------------------------------------------------


def judge(case: dict[str, Any], gold: dict[str, Any]) -> Verdict:
    """
    Judge one case.

    case  : arm_results.json item (canonical_object, answer, ...)
    gold  : R02_gold_v2.json item (id, status, event_time, evidence, ...)
    """
    cid = _norm(case.get("case_id") or gold.get("id"))
    v = Verdict(case_id=cid)

    co = case.get("canonical_object") or case.get("answer_object") or {}
    vr = case.get("validation_report") or {}
    if not isinstance(co, dict):
        v.blocking.append(Finding("answer_root_not_object", LOCUS_CANONICAL, BLOCKING, cid))
        return v
    if not isinstance(vr, dict):
        vr = {}
    statements: dict[str, str] = {}
    statement_sources: dict[str, str] = {}

    for ss in co.get("source_statement") if isinstance(co.get("source_statement"), list) else []:
        if not isinstance(ss, dict):
            continue
        sid = _norm(ss.get("id"))
        if sid:
            statements[sid] = _norm(ss.get("quote"))
            statement_sources[sid] = _norm(ss.get("source_id"))

    # published_at_raw lives in validation_report.program_source_metadata, keyed
    # by the same source_id the statements use (e.g. "S1", "S3#title")
    pub_by_source: dict[str, str] = {}
    for m in vr.get("program_source_metadata") if isinstance(vr.get("program_source_metadata"), list) else []:
        if not isinstance(m, dict):
            continue
        k = _norm(m.get("source_id"))
        p = _norm(m.get("published_at_raw"))
        if k and p:
            pub_by_source.setdefault(k, p)

    claims = [c for c in co.get("claims", []) if isinstance(c, dict)] if isinstance(co.get("claims"), list) else []
    fallback_pub = case.get("published_at_raw") or gold.get("published_at_raw")
    for c in claims:
        cited_ids = c.get("source_statement") if isinstance(c.get("source_statement"), list) else []
        cited_ids = [x for x in cited_ids if isinstance(x, str)]
        pubs = [pub_by_source[statement_sources[i]] for i in cited_ids
                if i in statement_sources and statement_sources[i] in pub_by_source]
        published = pubs[0] if pubs else fallback_pub
        check_date_claim(c, statements, published, cid, v, c.get("id"))

    check_narrative_date(_norm(co.get("answer")), statements, fallback_pub, cid, v)
    check_project_identity(claims, statements, statement_sources, cid, v)
    check_gold_expectation(_norm(gold.get("status")), _norm(co.get("answer_support")), cid, v)

    gt = normalize_gold_time(gold.get("event_time"))
    if not gt["parseable"]:
        v.advisory.append(Finding(
            "gold_time_not_parseable",
            LOCUS_GOLD, ADVISORY,
            f"{cid}: gold event_time '{gt['raw']}' -> caveat={gt['caveat']}; "
            "S3 cannot be compared on this label",
        ))
    if gt.get("provenance") == "derived_from_publication":
        v.advisory.append(Finding(
            "gold_time_borrows_publication_year",
            LOCUS_GOLD, ADVISORY,
            f"{cid}: gold event_time '{gt['raw']}' explicitly borrows the year "
            "from publication context; that annotation does not establish an event year. "
            "Old gold preserved for separate evidence-backed review",
        ))
    if gold_status_contradiction(_norm(gold.get("status"))):
        v.advisory.append(Finding(
            "gold_status_support_contradiction",
            LOCUS_GOLD, ADVISORY,
            f"{cid}: {gold_status_contradiction(_norm(gold.get('status')))}",
        ))

    return v


# --------------------------------------------------------------------------
# run stability classifier   (P1)
# --------------------------------------------------------------------------


def classify_stability(matrix: dict[str, list[str | None]]) -> dict[str, Any]:
    """Describe repeatability with explicit coverage and comparable denominators.

    These are technical gate outcomes, not measured model semantic accuracy.
    Missing/nonterminal observations are neither failures nor successes.
    """
    n = len(matrix)
    k = max((len(v) for v in matrix.values()), default=0)
    missing_tokens = {None, "", "not_run", "pending", "running", "reserved"}
    padded = {cid: list(v) + [None] * (k - len(v)) for cid, v in matrix.items()}
    complete = {cid: v for cid, v in padded.items() if k and not any(x in missing_tokens for x in v)}
    stable_pass = sorted(cid for cid, v in complete.items() if all(x == "completed" for x in v))
    stable_fail = sorted(cid for cid, v in complete.items() if all(x != "completed" for x in v))
    flaky = {cid: v for cid, v in sorted(complete.items()) if "completed" in v and any(x != "completed" for x in v)}
    incomplete = {cid: v for cid, v in sorted(padded.items()) if cid not in complete}
    union = sorted(cid for cid, v in padded.items() if "completed" in v)
    per_run = []
    for i in range(k):
        observed = {cid: v[i] for cid, v in padded.items() if v[i] not in missing_tokens}
        passed = sum(s == "completed" for s in observed.values())
        per_run.append({"run_index": i, "observed": len(observed), "missing": n-len(observed),
                        "passed": passed, "pass_rate": passed/len(observed) if observed else None})
    comparable_n = len(complete)
    return {
        "schema": SCHEMA, "replications": k, "n_cases": n,
        "comparable_cases": sorted(complete), "comparable_count": comparable_n,
        "stable_pass": stable_pass, "stable_fail": stable_fail, "flaky": flaky,
        "incomplete": incomplete, "incomplete_count": len(incomplete),
        "stable_pass_count": len(stable_pass), "stable_fail_count": len(stable_fail),
        "flaky_count": len(flaky), "per_run": per_run,
        "intersection_pass_count": len(stable_pass),
        "intersection_fraction": f"{len(stable_pass)}/{comparable_n}",
        "union_observed_pass": union, "union_observed_pass_count": len(union),
        "union_fraction": f"{len(union)}/{n}",
        "score_reportable": None,
        "score_best_of_k_not_reportable": None,
        "rule": "Intersection measures stability; per-run rates and observed union are separately labeled. "
                "None is a replacement semantic accuracy score; incomplete coverage is explicit.",
        "metric_basis": "technical_gate_status_not_independent_semantic_accuracy",
    }


def matrix_from_runs(run_states: dict[str, dict[str, str]]) -> dict[str, list[str | None]]:
    """Keep one position per run, including absent observations."""
    run_names = list(run_states)
    cases: dict[str, list[str | None]] = {}
    for i, run in enumerate(run_names):
        for slot, status in run_states[run].items():
            if isinstance(slot, str) and slot.startswith("formal_"):
                cid = slot.split("_", 1)[1]
                cases.setdefault(cid, [None] * len(run_names))[i] = status
    return cases


# --------------------------------------------------------------------------
# Production adapter: source-grounded diagnostics without gold or model calls
# --------------------------------------------------------------------------

def _base_source(source: str) -> str:
    for suffix in ("#title", "#publication"):
        if source.endswith(suffix):
            return source[:-len(suffix)]
    return source


def _items(value):
    return value if isinstance(value, list) else []


def _occurrences(text: str, quote: str):
    if not quote:
        return
    offset = 0
    while True:
        offset = text.find(quote, offset)
        if offset < 0:
            return
        yield offset
        offset += 1


def _local_context(text: str, start: int, end: int) -> str:
    # Keep comma-separated modality and its predicate together. Sentence
    # boundaries, not arbitrary character windows, delimit the check.
    left = max((text.rfind(c, 0, start) for c in "。；;！？!?\n"), default=-1) + 1
    right = end if end and text[end-1:end] in "。；;！？!?\n" else min((i for c in "。；;！？!?\n" if (i := text.find(c, end)) >= 0), default=len(text))
    return text[max(left, start-180):min(right, end+180)]


_ACTIONS = re.compile(r"竣工投产|建成投产|正式上线|成功交付|正式交付|开工|投产|上线|交付|签署|签约|收购|推出|采用|应用|举办|举行")
_ACTION_CANON = {"竣工投产": "投产", "建成投产": "投产", "正式上线": "上线",
                 "成功交付": "交付", "正式交付": "交付", "签署": "签约", "举办": "举行"}
_NEGATION = re.compile(r"(?:不能|无法|不足以|未能|不(?:能)?证明|不代表|不等于|尚未|并未|没有|未(?:确认|完成|投产|上线|交付|开工|签约)|不确认|否认)")
_PLAN = re.compile(r"将于|拟于|计划|预计|即将|有望|拟(?:建设|收购|投资)|可于")


def _action_facts(text: str):
    """Conservative local wording facts. These are not general semantic parses."""
    facts = []
    for m in _ACTIONS.finditer(text):
        left = max((text.rfind(c, 0, m.start()) for c in "。；;！？!?，,\n"), default=-1)+1
        right = min((i for c in "。；;！？!?，,\n" if (i := text.find(c, m.end())) >= 0), default=len(text))
        clause = text[left:right]
        before = text[left:m.start()]
        after = text[m.end():right]
        action = _ACTION_CANON.get(m.group(), m.group())
        # Narrow observed contrast scope: "而非已投产" denies this exact
        # commissioning predicate. Do not waive later affirmative predicates.
        direct_contrast_denial = action == "投产" and re.search(r"而非已\s*$", before) is not None
        if _NEGATION.search(before[-45:]) or direct_contrast_denial:
            state = "negated"
        elif re.search(r"完成后|建成后|投产后|若|如果|一旦|随着", before[-45:]) or re.match(r"后", after):
            state = "conditional"
        elif _PLAN.search(before[-45:]):
            state = "planned"
        elif re.search(r"(?:已(?:经)?|正式|成功|顺利|竣工|圆满|完成)", before[-25:]+m.group()) or action in ("开工", "签约", "举行"):
            state = "reported"
        elif action == "投产" and "庆典" in after[:10]:
            state = "reported"
        else:
            state = "mentioned"
        facts.append({"action": action, "state": state, "clause": clause,
                      "start": m.start(), "end": m.end()})
    return facts


def _quantity_values(text: str):
    units = {"亿元": ("money_cny", Decimal(100000000)), "万元": ("money_cny", Decimal(10000)),
             "元": ("money_cny", Decimal(1)), "万吨": ("mass_tonne", Decimal(10000)),
             "吨": ("mass_tonne", Decimal(1)), "%": ("percentage", Decimal(1)),
             "％": ("percentage", Decimal(1)), "台": ("count_unit", Decimal(1)),
             "寸": ("inch", Decimal(1)), "亩": ("mu", Decimal(1))}
    return [(m.group(), units[m[2]][0], Decimal(m[1])*units[m[2]][1])
            for m in re.finditer(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(亿元|万元|元|万吨|吨|%|％|台|寸|亩)", text)]


def evaluate_model_answer(answer_obj, prompt, full=None):
    """Validate one existing answer against its actual model-visible projection.

    `prompt` accepts the native payload or decoded prompt object. `full` is an
    optional provided-source record/registry used ONLY for context audits; it
    never retroactively becomes model-visible evidence. Gold is not accepted.
    The returned object never rewrites answer_obj, historical raw, or scores.
    """
    findings = []
    def add(code, locus=LOCUS_CANONICAL, severity=BLOCKING, detail="", **evidence):
        row = {"code": code, "locus": locus, "severity": severity, "detail": detail, **evidence}
        if row not in findings:
            findings.append(row)

    contract = _shared_module("contract")
    schema_errors = contract.validate_structure(answer_obj)
    for error in schema_errors:
        add("answer_schema_invalid", detail="model contract violation", schema_error=error)
    if isinstance(prompt, dict) and isinstance(prompt.get("prompt"), str):
        try:
            prompt = json.loads(prompt["prompt"])
        except (ValueError, TypeError):
            prompt = None
    elif isinstance(prompt, str):
        try:
            prompt = json.loads(prompt)
        except (ValueError, TypeError):
            prompt = None
    if not isinstance(prompt, dict):
        add("validation_input_invalid", "input_supply", detail="prompt is not a JSON object")
        prompt = {}
    source_defs = prompt.get("sources") if isinstance(prompt.get("sources"), dict) else {}
    source_defs = {sid: data for sid, data in source_defs.items() if isinstance(sid, str) and isinstance(data, dict)}
    full_record = full if isinstance(full, dict) else {}
    audit_metadata = full_record.get("source_metadata", {})
    if isinstance(full_record.get("full_original_provided_input"), dict):
        full_record = full_record["full_original_provided_input"]
    full_rows = [r for r in _items(full_record.get("registry"))
                 if isinstance(r, (list, tuple)) and len(r) == 4 and isinstance(r[0], str)
                 and type(r[1]) is int and type(r[2]) is int and isinstance(r[3], str)]
    full_by_source = {}
    for row in full_rows:
        full_by_source.setdefault(row[0], []).append(row)

    # Only visible metadata with an explicit publication role is admissible.
    question_role = _shared_module("date_roles").classify_question_date_role(_norm(prompt.get("question")))
    allows_pub = question_role in {"publication", "event_publication_comparison", "reporting_chronology"}
    pub_raw = prompt.get("publication") if isinstance(prompt.get("publication"), dict) else {}
    publication = {}
    for sid, data in pub_raw.items():
        if sid in source_defs and isinstance(data, dict) and allows_pub and data.get("role") == "publication_time_not_event_time" and data.get("model_generated") is False:
            raw = data.get("published_at")
            if isinstance(raw, str) and raw:
                publication[sid] = data
        else:
            add("publication_metadata_role_or_scope_invalid", "input_supply", detail="metadata cannot be used for this question/source", source_id=sid)

    # Every segment is literal model-visible text. A sanitizer may invalidate
    # original absolute offsets while leaving a verifiable supplied quote.
    segments = []
    for sid, data in source_defs.items():
        if isinstance(data.get("title"), str):
            segments.append({"src": sid+"#title", "start": 0, "end": len(data["title"]), "text": data["title"], "role": "title"})
    for card in _items(prompt.get("cards")):
        if not isinstance(card, dict) or not isinstance(card.get("src"), str) or not isinstance(card.get("text"), str):
            add("input_card_invalid", "input_supply", detail="card source/text missing or wrong type")
            continue
        if _base_source(card["src"]) not in source_defs:
            add("input_card_unknown_source", "input_supply", source_id=card["src"])
            continue
        if card["src"].endswith("#title"):
            if card["text"] not in source_defs[_base_source(card["src"])].get("title", ""):
                add("input_title_card_not_literal", "input_supply", source_id=card["src"])
            continue  # source_defs.title already supplies this exact field
        segments.append(dict(card))
    if "cards" not in prompt:
        for r in _items(prompt.get("registry")):
            if isinstance(r, (list, tuple)) and len(r) == 4 and isinstance(r[0], str) and isinstance(r[3], str):
                segments.append({"src": r[0], "start": r[1], "end": r[2], "text": r[3]})
    # The model contract requires one quote from one supplied card. Neighboring
    # cards may inform context checks but are never silently spliced into a
    # new admissible quotation.
    # Independently check supplied cards against the provided source registry.
    # Sanitized rows do not establish original/global coordinates.
    for segment in segments:
        source = segment["src"]
        base = _base_source(source)
        if source.endswith("#title"):
            known = (full_record.get("sources") or {}).get(base, {}) if isinstance(full_record.get("sources"), dict) else {}
            if isinstance(known, dict) and isinstance(known.get("title"), str) and segment["text"] != known["title"]:
                segment["_source_valid"] = False
                add("input_title_differs_from_provided_source", "input_supply", source_id=source)
            else:
                segment["_source_valid"] = True
            segment["_coordinates_verified"] = bool(known and segment["_source_valid"])
            continue
        rows = full_by_source.get(base, [])
        if not rows:
            segment["_source_valid"] = None
            segment["_coordinates_verified"] = False
            continue
        exact_coordinate = any(
            len(r[3]) == r[2]-r[1] and type(segment.get("start")) is int and type(segment.get("end")) is int
            and r[1] <= segment["start"] <= segment["end"] <= r[2]
            and r[3][segment["start"]-r[1]:segment["end"]-r[1]] == segment["text"]
            for r in rows)
        in_source = any(segment["text"] in r[3] for r in rows)
        covered_valid_region = any(len(r[3]) == r[2]-r[1] and type(segment.get("start")) is int and type(segment.get("end")) is int and r[1] <= segment["start"] <= segment["end"] <= r[2] for r in rows)
        covered_sanitized_region = any(len(r[3]) != r[2]-r[1] and type(segment.get("start")) is int and r[1] <= segment["start"] < r[2] for r in rows)
        segment["_source_valid"] = in_source if in_source or covered_valid_region else None
        segment["_coordinates_verified"] = exact_coordinate
        if not in_source:
            if covered_valid_region:
                add("input_card_not_literal_in_provided_source", "input_supply", source_id=source,
                    card_id=segment.get("id"), detail="a supplied, length-consistent source interval disagrees with this card")
            else:
                add("input_card_not_verifiable_after_sanitization" if covered_sanitized_region else "input_card_source_region_unavailable",
                    "input_supply", ADVISORY, source_id=source, card_id=segment.get("id"),
                    detail="the available registry does not preserve this full interval; do not infer fabrication or original-coordinate verification")
        elif not exact_coordinate:
            add("input_card_original_coordinates_unverified", "input_supply", ADVISORY,
                source_id=source, card_id=segment.get("id"), detail="literal source exists but redaction/offset drift prevents original coordinate proof")

    obj = answer_obj if isinstance(answer_obj, dict) else {}
    statements = [s for s in _items(obj.get("source_statement")) if isinstance(s, dict)]
    id_counts = {}
    for s in statements:
        if isinstance(s.get("id"), str):
            id_counts[s["id"]] = id_counts.get(s["id"], 0)+1
    duplicates = {sid for sid, count in id_counts.items() if count > 1}
    for sid in sorted(duplicates):
        add("duplicate_statement_id_no_selection", statement_id=sid)
    table, citations = {}, []
    for statement in statements:
        sid, source, quote = statement.get("id"), statement.get("source_id"), statement.get("quote")
        if not isinstance(sid, str) or not isinstance(source, str) or not isinstance(quote, str) or not quote:
            continue
        rec = {"statement_id": sid, "source_id": source, "quote": quote, "locations": [], "contexts": [], "valid": False}
        citations.append(rec)
        base = _base_source(source)
        if base not in source_defs:
            add("quote_unknown_input_source", statement_id=sid, source_id=source)
            continue
        if source.endswith("#publication"):
            metadata = publication.get(base)
            if metadata and quote == metadata.get("published_at"):
                rec.update(valid=True, kind="publication_metadata", locations=[{"source_id": source, "field": "published_at", "start": 0, "end": len(quote), "offset_space": "metadata_field"}], contexts=[quote])
                if statement.get("source_state") != "source_unknown":
                    add("publication_statement_is_not_event_state", statement_id=sid)
            else:
                add("publication_citation_not_supplied_or_not_exact", statement_id=sid, source_id=source)
        else:
            seen = set()
            occurrence_contexts = {}
            rec["source_contexts"] = []
            rec["full_source_candidates"] = []
            for segment_index, segment in enumerate(segments):
                if segment["src"] != source:
                    continue
                # A title segment is admissible on its own declared text. It is
                # deliberately not gated on _source_valid: that flag is about
                # whether the supplied title equals the registry record, which
                # must not silence a quotation that is literal in the title the
                # model actually received.
                if source.endswith("#title") and segment.get("role") != "title":
                    continue
                if not source.endswith("#title") and segment.get("_source_valid") is False:
                    continue
                for offset in _occurrences(segment["text"], quote):
                    absolute = segment.get("start", 0)+offset if type(segment.get("start")) is int else None
                    key = ((source, absolute, quote) if segment.get("_coordinates_verified") is True
                           else (source, (segment.get("id"), segment_index), offset, quote))
                    if key in seen:
                        continue
                    seen.add(key)
                    context = _local_context(segment["text"], offset, offset+len(quote))
                    # Extend to its observed source sentence when coordinates or
                    # a unique in-source text match allow it. Never cross source.
                    full_contexts = []
                    coordinates_valid = segment.get("_coordinates_verified") is True
                    for registry_index, r in enumerate(full_by_source.get(base, []) if not source.endswith("#title") else []):
                        for pos in _occurrences(r[3], quote):
                            if not coordinates_valid or r[1]+pos == absolute:
                                extended = _local_context(r[3], pos, pos+len(quote))
                                full_contexts.append(extended)
                                consistent_row = len(r[3]) == r[2]-r[1]
                                occurrence_key = ("source", r[1]+pos) if consistent_row else ("registry", registry_index, pos)
                                occurrence_contexts.setdefault(occurrence_key, set()).add(extended)
                                candidate = {"registry_index": registry_index, "start": r[1]+pos if consistent_row else pos,
                                             "end": r[1]+pos+len(quote) if consistent_row else pos+len(quote),
                                             "offset_space": "provided_source" if consistent_row else "provided_registry_segment"}
                                if candidate not in rec["full_source_candidates"]:
                                    rec["full_source_candidates"].append(candidate)
                    if not full_contexts:
                        occurrence_contexts.setdefault(key, set()).add(context)
                    for source_context in full_contexts or [context]:
                        if source_context not in rec["source_contexts"]:
                            rec["source_contexts"].append(source_context)
                    rec["locations"].append({"source_id": source, "start": absolute if coordinates_valid else offset, "end": absolute+len(quote) if coordinates_valid else offset+len(quote), "offset_space": "provided_source" if coordinates_valid else "supplied_segment_only", "original_coordinates_verified": coordinates_valid, "declared_source_start": absolute})
                    for observed_context in [context] + full_contexts:
                        if observed_context not in rec["contexts"]:
                            rec["contexts"].append(observed_context)
            # Length is a contract property, recorded independently of
            # locatability so that a long verbatim quotation cannot be reported
            # as fabricated merely because the length branch skips coordinate
            # lookup. Length, literal match, position and support are each
            # reported on their own axis.
            limit = _quote_limit()
            rec["quote_chars"] = len(quote)
            rec["quote_limit"] = limit
            if limit is not None and len(quote) > limit:
                add("quote_exceeds_contract_length", LOCUS_CANONICAL, ADVISORY,
                    statement_id=sid, source_id=source, length=len(quote), limit=limit,
                    detail="length violation recorded separately from literal match, position and support")
            rec["valid"] = bool(rec["locations"])
            rec["kind"] = "title" if source.endswith("#title") else "body"
            if not rec["valid"]:
                visible = _shared_module("visible_quotes").locate_visible_union(source, quote, segments, full_rows)
                for hit in visible:
                    rec["locations"].append(dict(hit, original_coordinates_verified=True, offset_space="provided_source"))
                    rec["literal_basis"] = "provided_source_and_contiguous_visible_cards"
                    r = full_rows[hit["registry_index"]]
                    extended = _local_context(r[3], hit["start"]-r[1], hit["end"]-r[1])
                    rec["contexts"].append(quote)
                    rec["source_contexts"].append(extended)
                    occurrence_contexts.setdefault(("source",hit["start"]),set()).add(extended)
                rec["valid"] = bool(rec["locations"])
            if not rec["valid"]:
                source_hits = [r for r in full_by_source.get(base,[]) if quote in r[3]]
                rec["literal_in_provided_source"] = bool(source_hits)
                rec["registry_scan_hits"] = sum(r[3].count(quote) for r in source_hits)
                rec["model_visible_coverage_complete"] = False
                add("quote_literal_but_not_fully_visible" if source_hits else "quote_not_literal_in_supplied_source",
                    statement_id=sid, source_id=source, quote=quote,
                    detail="source-only location does not prove this request supplied every character" if source_hits else "")
            elif len(occurrence_contexts) > 1:
                # Local and extended context for ONE occurrence are not two
                # occurrences. Compare only genuine location candidates.
                signatures = {tuple(sorted({(f["action"], f["state"]) for t in contexts for f in _action_facts(t)}))
                              for contexts in occurrence_contexts.values()}
                add("quote_occurrences_have_different_context" if len(signatures)>1 else "quote_multiple_occurrences_same_source", severity=BLOCKING if len(signatures)>1 else ADVISORY, statement_id=sid, occurrences=len(occurrence_contexts), detail="all observed location candidates retained; context expansion does not add an occurrence")
            title_text = source_defs.get(base, {}).get("title") if isinstance(source_defs.get(base), dict) else None
            if rec["valid"] and rec.get("kind") == "body" and isinstance(title_text, str) and title_text and quote in title_text:
                # Unambiguous only: the exact supplied title contains this exact
                # text. Nothing is rewritten here and no fuzzy match is offered,
                # so a source role cannot be invented from a partial overlap.
                add("title_quote_cited_as_body_source", severity=ADVISORY, statement_id=sid,
                    source_id=source, suggested_source_id=base+"#title",
                    detail="this exact text is supplied as the source title; cite it under the title role")
            rec["source_occurrence_count"] = len(occurrence_contexts)
            if rec["valid"]:
                quote_states = {f["state"] for f in _action_facts(quote)}
                contexts_states = {f["state"] for t in rec["source_contexts"] for f in _action_facts(t)}
                declared = statement.get("source_state")
                if declared == "source_reported_completed" and quote_states & {"planned", "conditional", "negated"}:
                    add("planned_or_negated_quote_mislabeled_completed", statement_id=sid)
                elif declared == "source_reported_completed" and not quote_states & {"planned", "conditional", "negated"} and contexts_states & {"planned", "conditional", "negated"}:
                    # A qualifier hidden by the quotation can change its meaning.
                    actions = {f["action"] for f in _action_facts(quote)}
                    context_facts = [f for t in rec["source_contexts"] for f in _action_facts(t) if f["action"] in actions]
                    if any(f["state"] in {"planned", "conditional", "negated"} for f in context_facts) and not any(f["state"] == "reported" for f in context_facts):
                        add("quote_omits_local_modality_qualifier", statement_id=sid, contexts=rec["contexts"])
                if declared == "source_planned" and quote_states == {"reported"} and any(f["action"] == "签约" for f in _action_facts(quote)):
                    add("signed_event_mislabeled_as_future_plan", statement_id=sid, detail="a signed investment agreement is not a completed investment project")
        if sid not in duplicates:
            table[sid] = {**statement, "_citation": rec}

    claims = [c for c in _items(obj.get("claims")) if isinstance(c, dict)]
    claim_ids = [c.get("id") for c in claims if isinstance(c.get("id"), str)]
    if len(claim_ids) != len(set(claim_ids)):
        add("duplicate_claim_id")
    claim_checks = []
    for index, claim in enumerate(claims):
        cid = claim.get("id", index)
        refs = claim.get("source_statement")
        if not isinstance(refs, list) or not refs:
            add("claim_statement_references_required", claim=cid)
            refs = []
        for ref in refs:
            if not isinstance(ref, str) or ref not in table or not table[ref]["_citation"]["valid"]:
                add("claim_statement_reference_invalid", claim=cid, reference=ref)
        selected = [table[r] for r in refs if isinstance(r, str) and r in table and table[r]["_citation"]["valid"]]
        body_selected = [s for s in selected if not s["source_id"].endswith("#publication")]
        quoted = " ".join(s["quote"] for s in body_selected)
        context_text = " ".join(t for s in body_selected for t in s["_citation"]["contexts"])
        bases = {_base_source(s["source_id"]) for s in body_selected}
        identities = [n for n in (claim.get("subject"), claim.get("project_identity")) if isinstance(n, str) and n]
        exact_names = [n for n in identities if n in quoted or n in context_text]
        if identities and not exact_names:
            add("claim_identity_not_literal_requires_context_review", severity=ADVISORY, claim=cid, identities=identities, detail="LCS overlap is not identity proof")
        # Explicit quantities/phases are distinguishing project anchors. A
        # common corporate name cannot erase an ungrounded project anchor.
        anchors = set(re.findall(r"\d+(?:\.\d+)?(?:万吨|吨|MW|GW|万平方米)|[一二三四五六七八九十\d]+期", " ".join(identities)))
        for anchor in sorted(anchors):
            if anchor not in quoted and anchor not in context_text:
                add("project_distinguishing_anchor_not_in_citations", claim=cid, anchor=anchor)
        for name in identities:
            if re.fullmatch(r"[\w\u4e00-\u9fff·&]{1,50}(?:公司|集团)", name) and name not in quoted and name not in context_text:
                add("named_subject_not_supported_by_citations", claim=cid, subject=name)
        if len(bases) > 1:
            add("cross_source_project_identity_requires_evidence", severity=ADVISORY, claim=cid, sources=sorted(bases), detail="same/different project_group and source folder are retrieval hints only")
        facts = [f for s in body_selected for f in _action_facts(s["quote"])]
        if claim.get("event_state") == "event_reported_completed" and body_selected and not facts:
            add("event_predicate_scope_requires_review", severity=ADVISORY, claim=cid,
                detail="known wording patterns cannot resolve this event predicate; absence from a verb list is not a semantic error")
        if claim.get("event_state") == "event_reported_completed" and facts and all(f["state"] in {"planned", "conditional", "negated"} for f in facts):
            add("completed_event_only_has_future_or_negated_evidence", claim=cid)
        if claim.get("event_state") == "event_reported_completed" and facts and {f["action"] for f in facts} <= {"开工", "签约"}:
            add("event_completion_scope_is_start_or_signing", severity=ADVISORY, claim=cid, detail="occurred opening/signing does not establish completed construction or commissioning")
        # event_construction_started is a MODALITY check: an action of
        # opening (开工) is only "started" when the source states it as
        # occurred/reported. Planned/conditional/negated opening wording
        # ("计划于...举行开工仪式") does not establish "started". The new
        # enum value must therefore mirror the event_reported_completed
        # modality check above, otherwise a plan is silently classified as
        # started.
        if claim.get("event_state") == "event_construction_started" and facts:
            non_occurred = [f for f in facts if f["state"] in {"planned", "conditional", "negated"}]
            if non_occurred and all(f["state"] in {"planned", "conditional", "negated"} for f in facts):
                add("started_event_only_has_future_or_negated_evidence", claim=cid,
                    detail="opening wording is planned/conditional/negated; does not establish event_construction_started")
            elif non_occurred and any(f["state"] == "reported" for f in facts):
                # Mixed modality: reported + planned in the same cited action
                # is a genuine conflict, not a "started" proof.
                add("started_event_source_modality_mixed_requires_review", severity=ADVISORY, claim=cid,
                    states=[f["state"] for f in facts],
                    detail="reported and planned/conditional/negated both appear for the same action; construction-start is not cleanly established")
        # event_signed modality mirror: a plan to sign is not signed.
        if claim.get("event_state") == "event_signed" and facts and all(f["state"] in {"planned", "conditional", "negated"} for f in facts):
            add("signed_event_only_has_future_or_negated_evidence", claim=cid)
        # Mirror of the completion check above. A signing state cannot be
        # established by construction-start, launch, delivery or commissioning
        # wording. Forcing a start event into the nearest available enum value
        # is an object/action/state confusion, not a wording quibble.
        declared_state = claim.get("event_state")
        observed_actions = {f["action"] for f in facts}
        if declared_state == "event_signed" and observed_actions and not observed_actions & _SIGNING_ONLY_ACTIONS:
            add("event_state_action_mismatch", claim=cid, event_state=declared_state,
                observed_actions=sorted(observed_actions),
                detail="a signing state is not established by construction-start, launch, delivery or commissioning wording")
        if (declared_state == "event_signed" and observed_actions
                and "event_construction_started" in _schema_event_states()
                and observed_actions & {"开工"}):
            add("event_signed_used_where_construction_start_state_exists", severity=ADVISORY, claim=cid,
                event_state=declared_state, observed_actions=sorted(observed_actions),
                detail="a dedicated construction-start state is legal in this schema; do not fold a start event into signing")
        for action in sorted(observed_actions):
            allowed = _ACTION_TO_STATES.get(action)
            if allowed is None or declared_state is None or declared_state in allowed:
                continue
            add("event_state_not_establishable_from_cited_action", severity=ADVISORY, claim=cid,
                event_state=declared_state, action=action, allowed=sorted(allowed),
                detail="stated state is outside the states this action wording can establish")
        ed = claim.get("explicit_event_date")
        if isinstance(ed, dict):
            date_refs = ed.get("evidence")
            if isinstance(date_refs, list):
                for ref in date_refs:
                    if not isinstance(ref, str) or ref not in refs:
                        add("date_evidence_not_bound_to_claim", claim=cid, reference=ref)
                    if isinstance(ref, str) and ref in table:
                        if table[ref]["source_id"].endswith("#publication"):
                            add("publication_reference_used_as_event_date", claim=cid, reference=ref)
                        elif re.search(r"发布日期|发表日期|发表于|发布于|发文时间|报道时间", table[ref]["quote"]) and not _action_facts(table[ref]["quote"]):
                            add("publication_wording_used_as_event_date", claim=cid, reference=ref)
            v = Verdict(str(cid))
            valid_quotes = {s["id"]: s["quote"] for s in body_selected}
            check_date_claim(claim, valid_quotes, None, "model", v, str(cid))
            findings.extend(f.as_dict() | {"claim": cid} for f in v.blocking+v.advisory)
        inferred = claim.get("inferred_event_date")
        if inferred is not None:
            add("publication_year_inference_not_event_evidence", claim=cid, detail="preserved old inferred field; publication year cannot establish event year")
        claim_checks.append({"claim_id": cid, "source_ids": sorted(bases), "identity_literal_matches": exact_names,
                             "observed_actions": sorted({f["action"] for f in facts}),
                             "identity_semantically_verified": False})

        # Relevant contrary wording is checked per source and action. Different
        # actions or projects in one article are not automatically conflicts.
        relevant_actions = {f["action"] for f in facts}
        for base in bases:
            all_visible = [s["text"] for s in segments if _base_source(s["src"]) == base]
            all_context = all_visible + [r[3] for r in full_by_source.get(base, [])]
            target_name = _norm(claim.get("project_identity")) or _norm(claim.get("subject"))
            project_named = bool(re.search(r"项目|官网|工厂|设备|检查机|产业园|基地|产品|系统|厂房", target_name))
            related = [t for t in all_context if (project_named and target_name and target_name in t) or any(s["quote"] in t for s in body_selected if _base_source(s["source_id"]) == base)]
            for action in relevant_actions:
                visible_facts = [f for t in related for f in _action_facts(t) if f["action"] == action]
                states = {f["state"] for f in visible_facts}
                if not ({"reported"} <= states and states & {"planned", "conditional", "negated"}):
                    continue
                cited_states = {f["state"] for s in body_selected if _base_source(s["source_id"]) == base for f in _action_facts(s["quote"]) if f["action"] == action}
                missing = (states & {"planned", "conditional", "negated", "reported"})-cited_states
                # Planned and conditional wording are both nonactual; an
                # already-cited plan is not defeated by a redundant future
                # benefits sentence. Keep actual versus nonactual opposition.
                if cited_states & {"planned", "conditional"}:
                    missing -= {"planned", "conditional"}
                if missing:
                    missing_in_visible = any(f["state"] in missing for t in all_visible for f in _action_facts(t) if f["action"] == action)
                    add("relevant_counterevidence_not_cited" if missing_in_visible else "relevant_counterevidence_not_supplied_to_model",
                        LOCUS_CANONICAL if missing_in_visible else "input_supply", BLOCKING if missing_in_visible else ADVISORY,
                        claim=cid, source_id=base, action=action, states_missing=sorted(missing))
                if claim.get("event_state") == "event_reported_completed" and obj.get("answer_support") != "evidence_conflicting":
                    add("source_opposite_wording_not_resolved", claim=cid, source_id=base, action=action)

    valid_statements = [s for s in table.values() if s["_citation"]["valid"]]
    if allows_pub and not publication:
        audit_pub = _shared_module("date_roles").normalize_publication_metadata(audit_metadata, source_defs)
        add("input_supply_publication_metadata_not_supplied", "input_supply", ADVISORY,
            detail="question asks publication/comparison/chronology but model input carries no publication metadata",
            available_in_provided_program_metadata=sorted(audit_pub))
    if question_role == "event_publication_comparison":
        target_sources = {_base_source(s["source_id"]) for s in valid_statements if not s["source_id"].endswith("#publication")}
        for base in sorted(target_sources):
            full_dates = {v for r in full_by_source.get(base, []) for p, v in _dates(r[3]) if p == "full_date"}
            supplied_dates = {v for seg in segments if _base_source(seg["src"]) == base for p, v in _dates(seg["text"]) if p == "full_date"}
            if full_dates and not supplied_dates:
                add("input_supply_event_date_omitted", "input_supply", ADVISORY, source_id=base,
                    dates_in_provided_source=sorted(full_dates), detail="event-date paragraphs exist in provided source but were absent from the model projection; original refusal is not a new model error")
    if obj.get("answer_support") == "evidence_supported" and not valid_statements:
        add("supported_answer_without_source_evidence", detail="supported negative answers are allowed but still need evidence")

    narrative = obj.get("answer") if isinstance(obj.get("answer"), str) else ""
    # Work per clause so a disclaimer elsewhere cannot launder an assertion.
    for sentence in re.split(r"[。；;！？!?\n]", narrative):
        inherited_source = None
        for part in re.split(r"[，,]", sentence):
            if not part.strip():
                continue
            mentioned = set(re.findall(r"(?<![A-Za-z0-9])S\d+(?![A-Za-z0-9])", part))
            if len(mentioned) == 1:
                inherited_source = next(iter(mentioned))
            scoped = mentioned or ({inherited_source} if inherited_source else set())
            bindings = [s for s in valid_statements if not scoped or _base_source(s["source_id"]) in scoped]
            body_bindings = [s for s in bindings if not s["source_id"].endswith("#publication")]
            cited = " ".join(s["quote"] for s in body_bindings)
            actions = _action_facts(part)
            positive_actions = [f for f in actions if f["state"] not in {"negated", "conditional", "planned"}]
            for source in scoped:
                if positive_actions and not any(_base_source(s["source_id"]) == source for s in body_bindings):
                    add("narrative_source_assertion_has_no_citation", LOCUS_NARRATIVE, source_id=source, clause=part)
            date_pairs = _dates(part)
            fulls = [v for p, v in date_pairs if p == "full_date"]
            candidates = [(p, v) for p, v in date_pairs if p == "full_date" or not fulls and p in {"year", "month_day"}]
            publication_clause = bool(re.search(r"发表|发布日期|发布日|发布于|发表于|报道时间|文章发布", part))
            # A year attached to reporting wording ("2022年8月的文章", "该文章
            # 的2022年") states a REPORTING TIME. It is not an unsupported
            # event date and must not be reported as one. Scoped to reporting
            # nouns only, so "2022年8月的投产计划" is still treated as an
            # event-time assertion.
            #
            # IMPORTANT: a prefix like "该文章提到" / "该文指出" only introduces
            # the source of an event and does NOT make the event date a
            # reporting date. "该文章提到X于2025年8月8日开工" — the 2025 is an
            # EVENT year bound to the action, not a publication year. The old
            # regex (r"(?:该|这|本)\s*(?:文章|报道|文|新闻)\s*(?:提及|提到|
            # 显示|指出|称|载|为)") incorrectly waived the whole clause as
            # reporting_time_clause, silently turning a real ungrounded event
            # year into an advisory. Removed in r2.2 revision 2.
            #
            # Reporting_time clauses are only those where the DATE is
            # syntactically bound to the reporting noun (before it via 的, or
            # after it as "文章的 YYYY 年"), never those where a reporting
            # phrase merely precedes an event description.
            reporting_time_clause = (not publication_clause) and bool(re.search(
                r"\d{4}\s*年\s*\d{1,2}\s*月?\s*的\s*(?:文章|报道|该文|本文|文中|新闻|材料|资讯)|"
                r"(?:文章|报道|新闻|材料|该文|本文|文中|资讯)\s*(?:的)?\s*\d{4}\s*年", part))
            qualified = bool(re.search(r"(?:不能|无法|尚未|未能|不宜).{0,10}(?:确认|确定|证明)|(?:不是|并非|不一定|假设|推测|可能).{0,12}\d", part))
            for precision, value in candidates:
                if qualified:
                    continue
                if reporting_time_clause:
                    # Role mismatch, not a missing event date. Boundable only
                    # against supplied publication metadata, so it stays
                    # advisory rather than blocking.
                    pub_bindings = [s for s in bindings if s["source_id"].endswith("#publication")]
                    if not any([precision, value] in _dates(s["quote"]) for s in pub_bindings):
                        add("narrative_reporting_time_not_bound_to_publication_metadata",
                            LOCUS_NARRATIVE, ADVISORY, value=value, precision=precision, clause=part,
                            detail="year attached to reporting wording; reported as reporting time, not as an ungrounded event date")
                    continue
                if publication_clause:
                    pub_bindings = [s for s in bindings if s["source_id"].endswith("#publication")]
                    supported = any([precision, value] in _dates(s["quote"]) for s in pub_bindings)
                    if not supported:
                        add("narrative_publication_date_not_bound_to_metadata", LOCUS_NARRATIVE, value=value, clause=part)
                elif not any([precision, value] in _dates(s["quote"]) for s in body_bindings):
                    elsewhere = [base for base, rows in full_by_source.items() if (not scoped or base in scoped) and any([precision, value] in _dates(r[3]) for r in rows)]
                    add("narrative_date_not_bound_to_citations", LOCUS_NARRATIVE, value=value, precision=precision, clause=part,
                        exists_elsewhere_in_provided_source=elsewhere, detail="question dates and publication years are not event evidence")
            for raw, dimension, value in _quantity_values(part):
                if not any(dimension == d and value == v for _, d, v in _quantity_values(cited)):
                    add("narrative_quantity_not_supported_by_citations", LOCUS_NARRATIVE, quantity=raw, clause=part)
            for fact in actions:
                matching = [f for s in body_bindings for f in _action_facts(s["quote"]) if f["action"] == fact["action"]]
                cited_actions = {f["action"] for s in body_bindings for f in _action_facts(s["quote"])}
                if fact["state"] == "reported" and fact["action"] == "投产" and not matching and cited_actions & {"开工", "签约"}:
                    add("narrative_promotes_start_or_signing_to_commissioning", LOCUS_NARRATIVE, clause=part,
                        detail="opening or signing evidence does not establish commissioning")
                if fact["state"] == "reported" and matching and all(f["state"] in {"planned", "conditional", "negated"} for f in matching):
                    add("narrative_completion_exceeds_cited_modality", LOCUS_NARRATIVE, action=fact["action"], clause=part)
                if fact["state"] == "planned" and re.search(r"确认可于", part) and any(f["state"] == "reported" for f in matching):
                    add("narrative_changes_reported_fact_to_future_possibility", LOCUS_NARRATIVE, action=fact["action"], clause=part)
                if fact["state"] == "reported" and obj.get("answer_support") == "evidence_conflicting" and not re.search(r"标题|首段|末段|正文|措辞|称|报道", part):
                    add("unqualified_narrative_completion_under_conflict", LOCUS_NARRATIVE, clause=part)
                # A conflict may not be listed and then resolved by majority or
                # by whichever side was published later. When opposite wording
                # for the same event is unresolved, the answer must stay
                # undetermined instead of confirming one side.
                if (fact["state"] == "reported" and obj.get("answer_support") != "evidence_conflicting"
                        and {f["code"] for f in findings} & {"source_opposite_wording_not_resolved", "relevant_counterevidence_not_cited"}
                        and not re.search(r"冲突|矛盾|不一致|尚未|未能|无法确认|有待|待确认|保留|未定", part)):
                    add("conflict_unresolved_narrative_asserts_confirmation", LOCUS_NARRATIVE, clause=part,
                        action=fact["action"], detail="opposite wording exists for the same event; the answer must stay undetermined rather than confirm one side")

    canonical_blocks = [f for f in findings if f["severity"] == BLOCKING and f["locus"] != LOCUS_NARRATIVE]
    narrative_blocks = [f for f in findings if f["severity"] == BLOCKING and f["locus"] == LOCUS_NARRATIVE]
    blocked = bool(canonical_blocks or narrative_blocks)
    return {
        "schema": "RAG2_semantic_validation_v1", "findings": findings,
        "schema_errors": schema_errors, "mechanical_valid": not schema_errors,
        "canonical_valid": not canonical_blocks, "narrative_valid": not narrative_blocks,
        "whole_answer_trust": "blocked" if blocked else "review_required",
        "answer_trust": "blocked" if blocked else "review_required", "confirmed": False,
        "answer_support_original": obj.get("answer_support"),
        "semantic_scope": "deterministic_limited_checks_not_independent_semantic_QA",
        "model_output_changed": False, "gold_used_for_validation": False,
        "branches": {"citations": citations, "claims": claim_checks, "question_date_role": question_role,
                     "publication_sources_supplied": sorted(publication),
                     "full_context_available": bool(full_rows),
                     "project_groups_used_as_identity_proof": False},
    }
