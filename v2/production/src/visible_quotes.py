"""Prove a quote is one source substring fully covered by visible card ranges.

No inference, whitespace normalization, gap filling or answer rewriting occurs.
Cross-card acceptance requires length-consistent registry coordinates and exact
card-to-source equality; a source-only hit is never model-visible evidence.
"""
from __future__ import annotations


def locate_visible_union(source_id, quote, cards, registry):
    if not isinstance(source_id, str) or source_id.endswith(('#title', '#publication')):
        return []
    if not isinstance(quote, str) or not quote:
        return []
    matches = []
    for index, row in enumerate(registry or []):
        if not isinstance(row, (list, tuple)) or len(row) != 4:
            continue
        sid, begin, end, text = row
        if (sid != source_id or type(begin) is not int or type(end) is not int
                or not isinstance(text, str) or end - begin != len(text)):
            continue
        verified = []
        for card_index, card in enumerate(cards or []):
            if not isinstance(card, dict) or card.get('src') != source_id:
                continue
            a, b, value = card.get('start'), card.get('end'), card.get('text')
            if (type(a) is not int or type(b) is not int or not isinstance(value, str)
                    or b - a != len(value) or not begin <= a <= b <= end):
                continue
            if text[a - begin:b - begin] == value:
                verified.append((a, b, card.get('id'), card_index))
        offset = text.find(quote)
        while offset >= 0:
            a, b = begin + offset, begin + offset + len(quote)
            cursor, used = a, []
            for left, right, card_id, card_index in sorted(verified, key=lambda x: (x[0], -x[1], x[3])):
                if right <= cursor or left >= b:
                    continue
                if left > cursor:
                    break  # Any unseen character invalidates this occurrence.
                used.append({'card_id': card_id, 'card_index': card_index,
                             'covered_start': cursor, 'covered_end': min(right, b)})
                cursor = min(right, b)
                if cursor == b:
                    matches.append({'source_id': source_id, 'start': a, 'end': b,
                                    'registry_index': index, 'covering_cards': used,
                                    'coordinate_scope': 'provided_source_registry',
                                    'source_coordinate_verified': True,
                                    'model_visible_coverage_complete': True,
                                    'literal_basis': 'one_source_continuous_text_and_visible_union'})
                    break
            offset = text.find(quote, offset + 1)
    return matches
