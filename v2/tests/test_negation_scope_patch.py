"""Narrow synthetic controls for the observed 而非已投产 false positive."""
import copy
from pathlib import Path
import sys
import unittest

FILES = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FILES / 'production/src'))
from runtime_paths import configure_imports
configure_imports()
import checker_v2 as C


def fixture(answer):
    text = '甲公司项目A于8月8日举行开工仪式。'
    prompt = {'question': '是否可确认项目A已投产？', 'sources': {'S1': {'title': '项目A开工', 'folder': '甲公司'}},
              'cards': [{'id': 'K1', 'src': 'S1', 'start': 0, 'end': len(text), 'text': text}]}
    full = {'sources': prompt['sources'], 'registry': [['S1', 0, len(text), text]]}
    obj = {'answer_support': 'evidence_supported', 'answer': answer,
           'source_statement': [{'id': 'E1', 'source_id': 'S1', 'quote': text, 'source_state': 'source_reported_completed'}],
           'claims': [{'id': 'C1', 'subject': '甲公司', 'project_identity': '项目A', 'event_state': 'event_construction_started',
                       'source_statement': ['E1'], 'explicit_event_date': {'value': '08-08', 'precision': 'month_day', 'evidence': ['E1']},
                       'inferred_event_date': None}], 'uncertainties': []}
    return obj, prompt, full


def codes(report):
    return {f['code'] for f in report['findings']}


class NegationScopePatchTests(unittest.TestCase):
    def evaluate(self, answer):
        obj, prompt, full = fixture(answer)
        original = copy.deepcopy(obj)
        report = C.evaluate_model_answer(obj, prompt, full)
        self.assertEqual(obj, original)
        self.assertFalse(report['confirmed'])
        return report

    def test_observed_direct_contrast_does_not_assert_commissioning(self):
        report = self.evaluate('材料表明项目A举行开工仪式，而非已投产状态。')
        self.assertNotIn('narrative_promotes_start_or_signing_to_commissioning', codes(report))

    def test_existing_cannot_prove_wording_remains_negative(self):
        report = self.evaluate('材料表明项目A举行开工仪式，不能证明已投产。')
        self.assertNotIn('narrative_promotes_start_or_signing_to_commissioning', codes(report))

    def test_affirmative_commissioning_still_blocked(self):
        report = self.evaluate('材料表明项目A举行开工仪式，且已投产。')
        self.assertIn('narrative_promotes_start_or_signing_to_commissioning', codes(report))
        self.assertEqual(report['whole_answer_trust'], 'blocked')

    def test_negative_clause_cannot_launder_later_affirmative_clause(self):
        report = self.evaluate('而非已投产，但另处又断言已投产。')
        self.assertIn('narrative_promotes_start_or_signing_to_commissioning', codes(report))

    def test_direct_contrast_cannot_launder_second_predicate_without_comma(self):
        facts = [f for f in C._action_facts('而非已投产但另处又断言已投产') if f['action'] == '投产']
        self.assertEqual([f['state'] for f in facts], ['negated', 'reported'])


if __name__ == '__main__':
    unittest.main()
