"""Rendering regressions: evidence visibility, safe text, honest denominators."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('rag2_results_page_tests', ROOT / 'candidate' / 'src' / 'make_results_page.py')
PAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PAGE)


class ResultsPageTests(unittest.TestCase):
    def test_import_has_no_missing_historical_path_reads(self):
        self.assertTrue(callable(PAGE.render_results))

    def test_complete_answer_citations_and_raw_score_remain_visible(self):
        data = {'cases': [{'case_id': 'D1', 'question': '问题？', 'technical_status': 'completed',
                'score': {'old': 0.75}, 'answer_object': {'answer': '原回答', 'answer_support': 'evidence_supported',
                'source_statement': [{'id': 'E1', 'source_id': 'S1', 'quote': '连续原文', 'source_state': 'source_planned'}],
                'claims': []}, 'semantic_validation': {'whole_answer_trust': 'review_required', 'mechanical_valid': True,
                'narrative_valid': True, 'findings': []}}], 'mode': 'historical_response_reassessment', 'new_model_calls': 0}
        result = PAGE.render_results(data)
        for required in ('问题？', '原回答', '连续原文', 'source_planned', '0.75', '待语义复核', '新增模型调用：0'):
            self.assertIn(required, result)
        self.assertIn('附独立复核记录 0', result)

    def test_completed_or_new_passed_does_not_mean_whole_answer_verified(self):
        result = PAGE.render_results([{'case_id': 'X', 'status': 'completed', 'new_passed': True, 'confirmed': True}])
        self.assertIn('未评估 1', result)
        self.assertIn('附独立复核记录 0', result)

    def test_composite_block_and_findings_survive_passing_semantic_child(self):
        row = {'whole_answer_trust': 'blocked', 'mechanical_valid': False,
               'historical_status': 'business_error', 'historical_technical_status': 'completed',
               'semantic_report': {'whole_answer_trust': 'review_required', 'mechanical_valid': True,
                                   'findings': [{'code': 'semantic_advisory'}]},
               'validation_report': {'whole_answer_trust': 'blocked', 'mechanical_valid': False,
                                     'risk_findings': [{'code': 'bad_reference'}]}}
        result = PAGE.render_results([row])
        self.assertIn('阻断 1', result)
        self.assertIn('待复核 0', result)
        self.assertIn('结构 / 引用等确定性检查</dt><dd>未通过', result)
        self.assertIn('bad_reference', result)
        self.assertIn('semantic_advisory', result)
        self.assertIn('historical_status', result)
        self.assertIn('historical_technical_status', result)

    def test_explained_independent_review_required_for_verified_flag(self):
        result = PAGE.render_results([{'whole_answer_trust': 'verified'}])
        self.assertIn('可信标记缺复核依据', result)
        result = PAGE.render_results([{'whole_answer_trust': 'verified', 'independent_semantic_review': {'status': 'approved', 'reference': 'audit-record-1'}}])
        self.assertIn('附独立复核记录 1', result)

    def test_untrusted_question_answer_findings_and_raw_are_escaped(self):
        payload = '</pre><script>alert("x")</script><img src=x onerror=alert(1)>'
        result = PAGE.render_results({'case_id': payload, 'question': payload, 'answer': payload,
            'semantic_validation': {'whole_answer_trust': 'blocked', 'findings': [{'code': payload, 'detail': payload}]}, 'raw_response': payload}, title=payload)
        self.assertNotIn('<script>', result)
        self.assertNotIn('<img ', result)
        self.assertIn('&lt;script&gt;', result)
        self.assertIn("default-src 'none'", result)

    def test_partial_and_malformed_rows_do_not_discard_other_answers(self):
        result = PAGE.render_results({'cases': [None, {'case_id': 'partial', 'error_class': 'transport_error'},
            {'case_id': 'good', 'answer': '后续记录仍保留', 'validation_report': None}]})
        self.assertIn('记录格式错误', result)
        self.assertIn('后续记录仍保留', result)
        self.assertIn('记录 3', result)

    def test_comparison_uses_observed_denominators_and_reports_duplicates(self):
        metrics = PAGE.comparison_metrics([
            {'case_id': 'A', 'old_mechanical_passed': True, 'new_passed': False},
            {'case_id': 'B', 'old_mechanical_passed': False, 'new_passed': True},
            {'case_id': 'A', 'old_mechanical_passed': True},
            {'case_id': 'C'},
        ])
        self.assertEqual(metrics['old_gate'], {'passed': 2, 'denominator': 3})
        self.assertEqual(metrics['new_gate'], {'passed': 1, 'denominator': 2})
        self.assertEqual(metrics['comparable_denominator'], 2)
        self.assertEqual(metrics['newly_blocked'], 1)
        self.assertEqual(metrics['newly_allowed'], 1)
        self.assertEqual(metrics['duplicate_case_ids'], ['A'])

    def test_no_hardcoded_run_scores_or_freeze_claims(self):
        result = PAGE.render_results([])
        for forbidden in ('14/20', '17/20', '229', '零漂移', '真正通过', 'NEW BLOCK', 'FIXED'):
            self.assertNotIn(forbidden, result)

    def test_page_cli_does_not_overwrite_input(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'data.json'
            path.write_text('[]', encoding='utf-8')
            with self.assertRaises(SystemExit):
                PAGE.main(['--input', str(path), '--output', str(path)])
            self.assertEqual(path.read_text(encoding="utf-8"), '[]')
            output = Path(folder) / 'result.html'
            self.assertEqual(PAGE.main(['--input', str(path), '--output', str(output)]), 0)
            self.assertIn('<!doctype html>', output.read_text(encoding="utf-8"))


if __name__ == '__main__':
    unittest.main()
