"""Portable production-path regressions; every fixture is synthetic."""
import copy
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import demo


class PublicPipelineTests(unittest.TestCase):
    def test_production_collector_preserves_answer_and_quarantines_bad_quote(self):
        cases = demo.synthetic_cases()
        original = copy.deepcopy(cases)
        with tempfile.TemporaryDirectory() as output, \
             patch.object(socket.socket, 'connect', side_effect=AssertionError('no network')), \
             patch.object(socket, 'create_connection', side_effect=AssertionError('no network')):
            result = demo.run(cases, output)
            self.assertEqual(result['new_model_calls'], 0)
            self.assertEqual(result['cases'][0]['whole_answer_trust'], 'review_required')
            self.assertEqual(result['cases'][1]['whole_answer_trust'], 'blocked')
            self.assertTrue(all(r['original_answer_unchanged'] for r in result['cases']))
            self.assertEqual(cases, original)
            html = (Path(output) / 'results.html').read_text(encoding='utf-8')
            self.assertIn('SYNTHETIC_REVIEW', html)
            self.assertIn('SYNTHETIC_BLOCKED', html)
            self.assertIn('default-src', html)


if __name__ == '__main__':
    unittest.main()
