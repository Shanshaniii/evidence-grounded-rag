"""Synthetic negative controls for the r2.2 corrections. SYNTHETIC ONLY.

Every fixture below is fabricated in this module. It is not derived from any
real source, real model response or gold label, and it is never merged into
the real 7-case wire set, the real sample corpus or any model score. Its only
purpose is to prove, offline and without a model call, that each relaxation
introduced by r2.2 still catches a genuinely defective case.

Standing rule being enforced here: a relaxation or exemption is only credible
if the companion test shows the historical-style defect is STILL blocked. Each
fix therefore ships with one "must still fire" case and one "must not fire"
case:

  1. conflict listed but resolved by majority        -> must still block
  2. conflict declared conflicting + undetermined    -> must NOT fire
  3. 开工 mislabeled as event_signed                 -> must still block
  4. 开工 correctly labeled event_construction_started-> must NOT fire
  5. article year read as event year                 -> advisory only
  6. ungrounded event year                           -> must still block
  7. long quote verbatim but outside the card window -> advisory, not not_literal
  8. long quote spliced across two adjacent cards    -> must still be not_literal
  9. long quote fabricated                           -> must still be not_literal
 10. title text cited under the body role            -> advisory role hint
 11. replay is deterministic and never mutates inputs

Run from files/ with the standard PYTHONPATH:
  python -m unittest tests.test_semantic_fixes_synthetic
"""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'candidate/src'))
import checker_v2 as C  # noqa: E402


def fixture(text='甲公司项目A于8月8日举行开工仪式。', *, question='项目A是否已建成投产？',
            title='项目A消息'):
    """One source, one body card covering the whole text, full registry in sync."""
    p = {'question': question,
         'sources': {'S1': {'title': title, 'folder': '目录公司'}},
         'cards': [{'id': 'K1', 'src': 'S1', 'start': 0, 'end': len(text), 'text': text, 'role': 'body'}]}
    a = {'answer_support': 'evidence_supported', 'answer': text,
         'source_statement': [{'id': 'E1', 'source_id': 'S1', 'quote': text,
                               'source_state': 'source_reported_completed'}],
         'claims': [{'id': 'C1', 'subject': '甲公司', 'project_identity': '项目A',
                     'event_state': 'event_reported_completed',
                     'source_statement': ['E1'],
                     'explicit_event_date': {'value': '08-08', 'precision': 'month_day', 'evidence': ['E1']},
                     'inferred_event_date': None}],
         'uncertainties': []}
    f = {'sources': copy.deepcopy(p['sources']), 'registry': [['S1', 0, len(text), text]]}
    return a, p, f


def codes(report, severity=None):
    return {x['code'] for x in report['findings'] if severity is None or x['severity'] == severity}


def conflict_setup():
    """One source carrying a reported sentence and an opposing planned sentence."""
    reported = '甲公司项目A于8月8日正式上线。'
    planned = '甲公司项目A计划于8月8日正式上线。'
    a, p, f = fixture(reported)
    a['answer'] = '甲公司项目A已于8月8日正式上线。'
    a['source_statement'][0]['quote'] = reported
    p['cards'].append({'id': 'K2', 'src': 'S1', 'start': 100, 'end': 100 + len(planned),
                       'text': planned, 'role': 'body'})
    f['registry'].append(['S1', 100, 100 + len(planned), planned])
    return a, p, f


class ConflictControls(unittest.TestCase):
    def test_conflict_detected_but_answer_confirms_is_still_blocked(self):
        a, p, f = conflict_setup()
        report = C.evaluate_model_answer(a, p, f)
        blockers = codes(report, severity='blocking')
        # The precondition must be present: the conflict is genuinely visible.
        self.assertTrue({'source_opposite_wording_not_resolved',
                         'relevant_counterevidence_not_cited'} & blockers, report['findings'])
        # ...and the narrative still asserting one side is itself blocking.
        self.assertIn('conflict_unresolved_narrative_asserts_confirmation', blockers)
        self.assertFalse(report['narrative_valid'])
        self.assertEqual(report['whole_answer_trust'], 'blocked')

    def test_declared_conflict_with_undetermined_answer_does_not_fire(self):
        a, p, f = conflict_setup()
        a['answer_support'] = 'evidence_conflicting'
        a['answer'] = '甲公司项目A于8月8日上线的报道存在冲突，尚未确认。'
        a['claims'][0]['event_state'] = 'event_conflicting'
        report = C.evaluate_model_answer(a, p, f)
        blockers = codes(report, severity='blocking')
        # Precondition unchanged: the opposing wording is still detected.
        self.assertTrue({'source_opposite_wording_not_resolved',
                         'relevant_counterevidence_not_cited'} & blockers, report['findings'])
        # A declared conflict with an undetermined answer is the correct response,
        # so the new narrative rule must not fire on it.
        self.assertNotIn('conflict_unresolved_narrative_asserts_confirmation', codes(report))


class EventStateControls(unittest.TestCase):
    def test_opening_mislabeled_as_signing_is_still_blocked(self):
        a, p, f = fixture()
        a['answer'] = '甲公司项目A已签约。'
        a['claims'][0]['event_state'] = 'event_signed'
        report = C.evaluate_model_answer(a, p, f)
        blockers = codes(report, severity='blocking')
        self.assertIn('event_state_action_mismatch', blockers)
        self.assertIn('event_signed_used_where_construction_start_state_exists',
                      codes(report, severity='advisory'))
        self.assertFalse(report['canonical_valid'])
        self.assertEqual(report['whole_answer_trust'], 'blocked')

    def test_dedicated_construction_start_state_is_accepted(self):
        a, p, f = fixture()
        a['answer'] = '甲公司项目A已于8月8日开工。'
        a['claims'][0]['event_state'] = 'event_construction_started'
        report = C.evaluate_model_answer(a, p, f)
        self.assertNotIn('event_state_action_mismatch', codes(report))
        self.assertNotIn('event_signed_used_where_construction_start_state_exists', codes(report))
        self.assertNotIn('event_state_not_establishable_from_cited_action', codes(report))


class NarrativeDateRoleControls(unittest.TestCase):
    def test_article_year_read_as_event_year_is_advisory_not_blocking(self):
        a, p, f = fixture()
        a['answer'] = '2022年8月的文章称，甲公司项目A于8月8日举行开工仪式。'
        a['source_statement'][0]['quote'] = '8月8日举行开工仪式'
        report = C.evaluate_model_answer(a, p, f)
        self.assertIn('narrative_reporting_time_not_bound_to_publication_metadata',
                      codes(report, severity='advisory'))
        # A reporting-time role mismatch is not a missing event date.
        self.assertNotIn('narrative_date_not_bound_to_citations', codes(report))
        self.assertTrue(report['narrative_valid'], report['findings'])

    def test_ungrounded_event_year_is_still_blocking(self):
        a, p, f = fixture()
        a['answer'] = '甲公司项目A于2022年8月8日举行开工仪式。'
        a['source_statement'][0]['quote'] = '8月8日举行开工仪式'
        report = C.evaluate_model_answer(a, p, f)
        self.assertIn('narrative_date_not_bound_to_citations', codes(report, severity='blocking'))
        self.assertFalse(report['narrative_valid'])
        self.assertEqual(report['whole_answer_trust'], 'blocked')


class LongQuoteControls(unittest.TestCase):
    LONG = ('2022年6月2日，内蒙古自治区呼和浩特市政府及托克托县相关负责人出席，'
            '甲公司项目A举行开工仪式并现场签署合作协议。同时双方就后续产能建设、'
            '设备交付时间以及试生产验收标准达成一致意见。')

    def _windowed(self):
        a, p, f = fixture(self.LONG)
        a['answer'] = '项目A已于2022年6月2日举行开工仪式。'
        a['source_statement'][0]['quote'] = self.LONG
        window = 25
        p['cards'] = [{'id': 'K1', 'src': 'S1', 'start': 0, 'end': window,
                       'text': self.LONG[:window], 'role': 'body'}]
        return a, p, f

    def test_long_quote_verbatim_but_outside_card_window_is_advisory(self):
        a, p, f = self._windowed()
        report = C.evaluate_model_answer(a, p, f)
        self.assertGreater(len(self.LONG), C._quote_limit())
        # Length is its own axis, reported advisory.
        self.assertIn('quote_exceeds_contract_length', codes(report, severity='advisory'))
        # The contract limit is still enforced -- the length violation is not
        # hidden, it is just no longer misreported as fabrication.
        self.assertIn('answer_schema_invalid', codes(report))
        self.assertEqual(report['schema_errors'], [{'path': '$.source_statement[0].quote', 'code': 'length'}])
        # The quote is verbatim in the supplied source, so it is NOT fabricated.
        self.assertNotIn('quote_not_literal_in_supplied_source', codes(report))
        citation = report['branches']['citations'][0]
        self.assertTrue(citation['valid'])
        self.assertEqual(citation['literal_basis'], 'provided_source_only')
        self.assertEqual(len(citation['locations']), 1)

    def test_long_quote_spliced_across_two_adjacent_cards_is_still_not_literal(self):
        a, p, f = self._windowed()
        split = 40
        p['cards'] = [{'id': 'K1', 'src': 'S1', 'start': 0, 'end': split,
                       'text': self.LONG[:split], 'role': 'body'},
                      {'id': 'K2', 'src': 'S1', 'start': split, 'end': len(self.LONG),
                       'text': self.LONG[split:], 'role': 'body'}]
        report = C.evaluate_model_answer(a, p, f)
        # Crossing a card boundary is a splice and must not be blessed by the
        # provided-source fallback, even when the quote is verbatim there.
        self.assertIn('quote_not_literal_in_supplied_source', codes(report, severity='blocking'))
        self.assertNotIn('quote_literal_in_provided_source_but_absent_from_supplied_card',
                         codes(report))
        self.assertFalse(report['branches']['citations'][0]['valid'])

    def test_long_quote_that_is_fabricated_is_still_not_literal(self):
        a, p, f = fixture()
        fabricated = ('甲公司项目A已完成全部建设工作并投入生产运营，一期产能全面释放，'
                      '年产能力达到设计水平的百分之百，且已通过客户验收合格，'
                      '后续二期工程也已完成设备调试')
        self.assertGreater(len(fabricated), C._quote_limit())
        a['source_statement'][0]['quote'] = fabricated
        a['answer'] = '项目A已投产。'
        report = C.evaluate_model_answer(a, p, f)
        self.assertIn('quote_not_literal_in_supplied_source', codes(report, severity='blocking'))
        self.assertNotIn('quote_literal_in_provided_source_but_absent_from_supplied_card',
                         codes(report))
        self.assertEqual(report['branches']['citations'][0]['registry_scan_hits'], 0)


class SourceRoleControls(unittest.TestCase):
    def test_title_text_cited_under_body_role_gets_advisory_hint(self):
        title = '甲公司项目A投产庆典举行'
        body = '2024年8月20日，' + title + '，多位领导出席。'
        a, p, f = fixture(body, title=title)
        a['answer'] = '项目A举行投产庆典。'
        a['source_statement'][0]['quote'] = title
        report = C.evaluate_model_answer(a, p, f)
        finding = [x for x in report['findings'] if x['code'] == 'title_quote_cited_as_body_source']
        self.assertEqual(len(finding), 1)
        self.assertEqual(finding[0]['severity'], 'advisory')
        self.assertEqual(finding[0]['suggested_source_id'], 'S1#title')
        # A role hint is advisory: it never rewrites the citation and never
        # invents a source role from a partial overlap.
        self.assertFalse(report['model_output_changed'])

    def test_partial_title_overlap_does_not_get_role_hint(self):
        title = '甲公司项目A投产庆典举行'
        body = '2024年8月20日，' + title + '，多位领导出席。'
        a, p, f = fixture(body, title=title)
        a['answer'] = '项目A举行投产庆典。'
        a['source_statement'][0]['quote'] = '项目A投产庆典举行，多位'
        report = C.evaluate_model_answer(a, p, f)
        self.assertNotIn('title_quote_cited_as_body_source', codes(report))


class ReplayControls(unittest.TestCase):
    def test_replay_is_deterministic_and_never_mutates_inputs(self):
        a, p, f = conflict_setup()
        before = copy.deepcopy((a, p, f))
        first = C.evaluate_model_answer(a, p, f)
        second = C.evaluate_model_answer(a, p, f)
        # Same inputs, same verdicts: scoring is reproducible.
        self.assertEqual(json.dumps(first, sort_keys=True, ensure_ascii=False),
                         json.dumps(second, sort_keys=True, ensure_ascii=False))
        # Replaying must not touch the model output or the supplied input.
        self.assertFalse(first['model_output_changed'])
        self.assertFalse(second['model_output_changed'])
        self.assertEqual((a, p, f), before)
        self.assertFalse(first['gold_used_for_validation'])

    def test_replay_of_blocked_case_stays_blocked_across_repeats(self):
        a, p, f = fixture()
        a['answer'] = '甲公司项目A已投产。'
        verdicts = [C.evaluate_model_answer(copy.deepcopy(a), copy.deepcopy(p), copy.deepcopy(f))['whole_answer_trust']
                    for _ in range(3)]
        self.assertEqual(verdicts, ['blocked', 'blocked', 'blocked'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
