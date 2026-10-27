"""Deterministic, continuous source-local cards from the provided text only.

Title similarity is a retrieval hint, never project-identity evidence. Folder
names do not identify fact subjects and do not exclude another folder's evidence.
The original public ``dates``, ``slices``, ``group_ids`` and ``select`` APIs remain.
"""
from __future__ import annotations

import collections
import math
import re
from typing import Any

from date_roles import extract_literal_dates

CARD_LIMIT = 64
GROUP_THRESHOLD = 0.5
SENT_SPLIT = re.compile(r"[^。！？;；\n]+[。！？;；]?|\n")
SUB_SPLIT = re.compile(r"[^，、：—]+[，、：—]?|—+")
_EVENT = re.compile(r"投产|上线|开工|签约|签署|交付|建成|完成|投资协议|启用|投入运营")
_QUALIFIER = re.compile(r"将|拟|计划|预计|建成后|随着|未|尚未|没有|无法|不能|条件|有待")


def dates(text):
    """Compatibility wrapper for the shared strict date parser."""
    return extract_literal_dates(text)


def terms(text):
    out = []
    for run in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+", str(text)):
        out.extend([run] if not re.search(r"[\u4e00-\u9fff]", run)
                   else [run[i:i + 2] for i in range(len(run) - 1)])
    return set(out)


def slices(text, base, limit=CARD_LIMIT):
    """Clause-aligned slices; every output is an exact continuous input span."""
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("card_limit_must_be_positive")
    parts = []
    for seg in SENT_SPLIT.finditer(text):
        chunk = seg.group()
        if not chunk.strip():
            continue
        if len(chunk) <= limit:
            parts.append((base + seg.start(), chunk))
            continue
        buf, bufstart = "", base + seg.start()
        for sub in SUB_SPLIT.finditer(chunk):
            part = sub.group()
            if not part:
                continue
            if buf and len(buf) + len(part) > limit:
                if buf.strip():
                    parts.append((bufstart, buf))
                bufstart, buf = base + seg.start() + sub.start(), part
            else:
                if not buf:
                    bufstart = base + seg.start() + sub.start()
                buf += part
            while len(buf) > limit:
                parts.append((bufstart, buf[:limit]))
                bufstart, buf = bufstart + limit, buf[limit:]
        if buf.strip():
            parts.append((bufstart, buf))
    return parts


def folders_of(bundle):
    return {sid: str(item.get("folder") or "") for sid, item in bundle["sources"].items()}


def main_folders(bundle):
    """Query-mentioned folder names: ranking hints only, never exclusion rules."""
    question = bundle["question"]
    return {str(item.get("folder") or "").strip() for item in bundle["sources"].values()
            if str(item.get("folder") or "").strip()
            and str(item.get("folder") or "").strip() in question}


def group_ids(bundle):
    """Legacy deterministic title-similarity groups, explicitly non-authoritative."""
    source_ids = sorted(bundle["sources"])
    parent = {sid: sid for sid in source_ids}

    def find(sid):
        while parent[sid] != sid:
            sid = parent[sid]
        return sid

    key = lambda sid: terms(bundle["sources"][sid].get("title", ""))
    for i, left in enumerate(source_ids):
        for right in source_ids[i + 1:]:
            if bundle["sources"][left].get("folder", "") != bundle["sources"][right].get("folder", ""):
                continue
            left_terms, right_terms = key(left), key(right)
            score = len(left_terms & right_terms) / len(left_terms | right_terms) if left_terms | right_terms else 0
            if score >= GROUP_THRESHOLD:
                parent[find(left)] = find(right)
    groups = {}
    for sid in source_ids:
        root = find(sid)
        groups.setdefault(root, "G" + str(len(groups) + 1))
    return {sid: groups[find(sid)] for sid in source_ids}


def select(bundle, *, extra_cards=8, max_cards=None, max_body_chars=None):
    """Select boundaries, query hits, linked sentence context and qualifications.

    Optional limits are *inspection* limits: evidence is not silently deleted to
    squeeze beneath them. ``selection_limit_exceeded`` tells the old preflight /
    caller to defer the request. No gold, status labels or model responses enter.
    """
    if not isinstance(bundle, dict) or not isinstance(bundle.get("question"), str):
        raise ValueError("invalid_full_input_question")
    if not isinstance(bundle.get("sources"), dict) or not isinstance(bundle.get("registry"), list):
        raise ValueError("invalid_full_input_sources_or_registry")
    if any(not isinstance(sid, str) or not isinstance(source, dict)
           for sid, source in bundle["sources"].items()):
        raise ValueError("invalid_source_record")
    if not isinstance(extra_cards, int) or isinstance(extra_cards, bool) or extra_cards < 0:
        raise ValueError("invalid_extra_card_count")
    for limit in (max_cards, max_body_chars):
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("invalid_selection_limit")
    folders, groups, main = folders_of(bundle), group_ids(bundle), main_folders(bundle)
    pool, issues = [], []
    for sid, source in bundle["sources"].items():
        title = source.get("title", "")
        if not isinstance(sid, str) or not isinstance(title, str):
            raise ValueError("invalid_source_identifier_or_title")
        pool.append({"id": sid + "_TITLE", "src": sid + "#title", "start": 0,
                     "end": len(title), "text": title, "literal_dates": dates(title),
                     "role": "title", "folder": folders[sid], "project_group": groups[sid],
                     "offset_provenance": "provided_title"})
    for index, row in enumerate(bundle["registry"]):
        if (not isinstance(row, (list, tuple)) or len(row) != 4 or row[0] not in folders
                or type(row[1]) is not int or type(row[2]) is not int
                or row[1] < 0 or row[2] < row[1] or not isinstance(row[3], str)):
            issues.append({"registry_index": index, "code": "invalid_registry_row"})
            continue
        sid, start, end, text = row
        consistent = end - start == len(text)
        if not consistent:
            issues.append({"registry_index": index, "source_id": sid,
                           "code": "provided_end_length_mismatch", "declared_start": start,
                           "declared_end": end, "available_text_length": len(text)})
        sentence_ranges = [(m.start(), m.end()) for m in SENT_SPLIT.finditer(text) if m.group().strip()]
        for offset, part in slices(text, start):
            local = offset - start
            sentence_start = next((lo for lo, hi in sentence_ranges if lo <= local < hi), local)
            pool.append({"id": sid + "_P" + str(offset), "src": sid,
                         "start": offset, "end": offset + len(part), "text": part,
                         "literal_dates": dates(part), "role": "body", "folder": folders[sid],
                         "project_group": groups[sid], "context_id": f"{sid}_R{index}_S{sentence_start}",
                         "registry_index": index, "segment_start": local,
                         "offset_provenance": "provided_registry" if consistent else "sanitized_segment"})
    # Repeated/overlapping registry entries must not silently overwrite a card.
    seen = collections.Counter()
    for card in pool:
        seen[card["id"]] += 1
        if seen[card["id"]] > 1:
            card["id"] += "_R" + str(card.get("registry_index", seen[card["id"]]))
    query_terms = terms(bundle["question"])
    df = collections.Counter(term for card in pool for term in terms(card["text"]))

    def score(card):
        return sum(math.log((len(pool) + 1) / (df[term] + 1))
                   for term in terms(card["text"]) & query_terms)

    source_scores = {sid: max((score(card) for card in pool
                              if card["src"].split("#")[0] == sid), default=0)
                     for sid in folders}
    top_source_score = max(source_scores.values(), default=0)
    focused_sources = {sid for sid, value in source_scores.items()
                       if top_source_score == 0 or value >= top_source_score * 0.4}

    chosen = {}
    reasons = collections.defaultdict(list)
    by_context = collections.defaultdict(list)
    for card in pool:
        if card.get("context_id"):
            by_context[card["context_id"]].append(card)

    def include(card, reason, role=None):
        saved = chosen.setdefault(card["id"], dict(card))
        if role and saved["role"] == "body":
            saved["role"] = role
        if reason not in reasons[card["id"]]:
            reasons[card["id"]].append(reason)

    for card in pool:
        if card["role"] == "title":
            include(card, "source_title")
    boundaries = []
    for row in bundle.get("boundaries", []):
        if not isinstance(row, (list, tuple)) or len(row) != 5:
            issues.append({"code": "invalid_boundary_row"})
            continue
        sid, first_start, first_end, last_start, last_end = row
        if sid not in folders or not all(type(x) is int for x in row[1:]):
            issues.append({"code": "invalid_boundary_row", "source_id": str(sid)})
            continue
        picked_regions = []
        for low, high, role in ((first_start, first_end, "first"), (last_start, last_end, "last")):
            available = [card for card in pool if card["src"] == sid
                         and card["start"] < high and card["end"] > low]
            if not available:
                issues.append({"code": "boundary_text_unavailable", "source_id": sid,
                               "role": role, "start": low, "end": high})
                picked_regions.append((low, high))
                continue
            # Keep the old first/last lexical anchor. Expand its complete sentence
            # for query-relevant sources below; don't spend the whole budget on
            # unrelated participant lists and corporate slogans.
            anchor = min(available, key=lambda card: (-score(card), card["start"], card["id"]))
            include(anchor, "boundary_" + role, role)
            picked_regions.append((anchor["start"], anchor["end"]))
        boundaries.append([sid, *picked_regions[0], *picked_regions[1]])
    ranked = sorted((card for card in pool if card["role"] != "title"),
                    key=lambda card: (-score(card), card["src"], card["start"], card["id"]))
    selected_hits = [card for card in ranked if score(card) > 0
                     and card["src"] in focused_sources][:extra_cards]
    for card in selected_hits:
        include(card, "query_relevance")
    # Preserve a complete sentence around a selected fragment: the date, subject,
    # verb, negation or conditional may have fallen in an adjacent <=64-char card.
    for card in list(chosen.values()):
        if card["src"] in focused_sources:
            for sibling in by_context.get(card.get("context_id"), []):
                include(sibling, "linked_sentence_context")
    # Additional relevant qualification candidates, with a transparent audit.
    events = set(_EVENT.findall(bundle["question"]))
    counter_candidates = [card for card in ranked if _QUALIFIER.search(card["text"])
                          and any(event in card["text"] for event in events)
                          and score(card) > 0 and card["src"] in focused_sources]
    for card in counter_candidates[:4]:
        include(card, "relevant_qualification")
        for sibling in by_context.get(card.get("context_id"), []):
            include(sibling, "qualification_sentence_context")
    selected = list(chosen.values())
    body_chars = sum(len(card["text"]) for card in selected if card["role"] != "title")
    exceeded = ((max_cards is not None and len(selected) > max_cards)
                or (max_body_chars is not None and body_chars > max_body_chars))
    meta: dict[str, Any] = {
        "main_folders": sorted(main), "scoped_out_sources": [],
        "group_by_source": groups, "folder_by_source": folders,
        "project_groups_are_identity_proof": False, "grouping_basis": "title_similarity_hint",
        "clause_aligned_slicing": True, "group_threshold": GROUP_THRESHOLD,
        "selection_rule": "all source titles and first/last lexical anchors; query-focused sources (>=0.4 of max source relevance) supply top lexical hits with continuous sentence context and relevant qualifications; no folder exclusion or QA labels",
        "context_expanded_sources": sorted(focused_sources),
        "context_deprioritized_sources": sorted(set(folders) - focused_sources),
        "source_relevance_scores": source_scores,
        "selection_reasons": dict(reasons), "registry_issues": issues,
        "selected_card_count": len(selected), "selected_body_chars": body_chars,
        "selection_limit_exceeded": bool(exceeded),
        "omitted_relevant_qualifications": [card["id"] for card in counter_candidates if card["id"] not in chosen],
        "retrieval_complete": len(chosen) == len(pool),
    }
    return selected, boundaries, pool, meta
