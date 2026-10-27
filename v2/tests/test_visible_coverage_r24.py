"""Authorized coverage contract: fabricated fixtures, no model score or repair."""
import copy
import json
from pathlib import Path
import sys
import unittest

FILES = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FILES / 'production/src'))
from runtime_paths import configure_imports
configure_imports()
from diagnostics import evaluate
from contract import validate_structure
from visible_quotes import locate_visible_union


TEXT = '甲公司项目A于8月8日正式上线，' + '本项目覆盖了研发、测试、部署、运行及维护等多个环节，相关工作由甲公司自行实施，' + '相关运行记录包含服务质量与业务流程的统计资料。'


def fixture(parts=None):
    cards = [{'id': str(i), 'src': 'S1', 'start': a, 'end': b, 'text': TEXT[a:b]}
             for i, (a, b) in enumerate(parts or [(0, len(TEXT))])]
    prompt = {'question': '项目A何时上线？', 'sources': {'S1': {'title': '项目A消息', 'folder': '甲公司'}}, 'cards': cards}
    full = {'sources': prompt['sources'], 'registry': [['S1', 0, len(TEXT), TEXT]]}
    answer = {'answer_support': 'evidence_supported', 'answer': '甲公司项目A于8月8日正式上线，原文未给出年份。',
              'source_statement': [{'id': 'E1', 'source_id': 'S1', 'quote': TEXT, 'source_state': 'source_reported_completed'}],
              'claims': [{'id': 'C1', 'subject': '甲公司', 'project_identity': '项目A', 'event_state': 'event_reported_completed',
                          'source_statement': ['E1'], 'explicit_event_date': {'value': '08-08', 'precision': 'month_day', 'evidence': ['E1']},
                          'inferred_event_date': None}], 'uncertainties': ['事件年份未提供。']}
    return answer, {'prompt': json.dumps(prompt, ensure_ascii=False)}, full


class VisibleCoverageTests(unittest.TestCase):
    def assert_passes(self, parts):
        answer, payload, full = fixture(parts)
        original = copy.deepcopy(answer)
        self.assertGreater(len(TEXT), 64)
        report = evaluate(answer, payload, full)
        self.assertEqual(report['whole_answer_trust'], 'review_required', report['risk_findings'])
        self.assertFalse(report['confirmed'])
        self.assertEqual(answer, original)
        return report

    def test_long_true_quote_in_single_card_has_no_arbitrary_limit(self):
        self.assert_passes([(0, len(TEXT))])

    def test_adjacent_visible_cards_cover_every_character(self):
        report = self.assert_passes([(0, 39), (39, len(TEXT))])
        self.assertTrue(report['program_locations'][0]['location']['selected_span']['model_visible_coverage_complete'])

    def test_overlapping_visible_cards_are_valid(self):
        self.assert_passes([(0, 50), (35, len(TEXT))])

    def test_unseen_one_character_gap_is_blocked_even_when_full_text_exists(self):
        answer, payload, full = fixture([(0, 39), (40, len(TEXT))])
        report = evaluate(answer, payload, full)
        self.assertEqual(report['whole_answer_trust'], 'blocked')
        self.assertIn('quote_literal_but_not_fully_visible', {f['code'] for f in report['risk_findings']})

    def test_cards_of_different_source_cannot_fill_a_gap(self):
        answer, payload, full = fixture([(0, 39), (39, len(TEXT))])
        prompt = json.loads(payload['prompt'])
        prompt['cards'][1]['src'] = 'S2'
        prompt['sources']['S2'] = {'title': '另一来源'}
        payload['prompt'] = json.dumps(prompt, ensure_ascii=False)
        self.assertEqual(evaluate(answer, payload, full)['whole_answer_trust'], 'blocked')

    def test_deleted_middle_is_not_a_continuous_source_quote(self):
        answer, payload, full = fixture()
        answer['source_statement'][0]['quote'] = TEXT[:25] + TEXT[40:]
        report = evaluate(answer, payload, full)
        self.assertEqual(report['whole_answer_trust'], 'blocked')
        self.assertIn('quote_not_literal_in_supplied_source', {f['code'] for f in report['risk_findings']})

    def test_full_source_without_visible_coverage_is_not_promoted(self):
        answer, payload, full = fixture([(0, 10)])
        report = evaluate(answer, payload, full)
        self.assertEqual(report['whole_answer_trust'], 'blocked')
        self.assertFalse(report['program_locations'][0]['location']['supplied_context_valid'])

    def test_forged_overlapping_card_cannot_prove_coverage(self):
        answer, payload, full = fixture([(0, 39), (39, len(TEXT))])
        prompt = json.loads(payload['prompt'])
        prompt['cards'][1]['text'] = '伪' + prompt['cards'][1]['text'][1:]
        self.assertEqual(locate_visible_union('S1', TEXT, prompt['cards'], full['registry']), [])

    def test_answer_longer_than_old_100_is_not_schema_error(self):
        answer, _, _ = fixture()
        answer['answer'] = '合法内容。' * 30
        self.assertEqual(validate_structure(answer), [])


if __name__ == '__main__':
    unittest.main()

