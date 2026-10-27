"""Portable V2 demonstration using production diagnostics and synthetic data.

No model or network is used. --input accepts locally supplied case records;
the default fixture is explicitly fabricated and is not a model quality score.
"""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'production/src'))
from runtime_paths import configure_imports
configure_imports()
from collector_v04 import collect_v04
from make_results_page import write_results_page


def synthetic_cases():
    text = '甲公司项目A于8月8日举行开工仪式。'
    sources = {'S1': {'title': '项目A消息', 'folder': '甲公司'}}
    card = {'id': 'K1', 'src': 'S1', 'start': 0, 'end': len(text), 'text': text, 'role': 'body'}
    prompt = {'question': '项目A发生了什么，何时发生？', 'sources': sources, 'cards': [card]}
    full = {'sources': sources, 'registry': [['S1', 0, len(text), text]]}
    answer = {'answer_support': 'evidence_supported', 'answer': text,
              'source_statement': [{'id': 'E1', 'source_id': 'S1', 'quote': text, 'source_state': 'source_unknown'}],
              'claims': [{'id': 'C1', 'subject': '甲公司', 'project_identity': '项目A',
                          'event_state': 'event_construction_started', 'source_statement': ['E1'],
                          'explicit_event_date': {'value': '08-08', 'precision': 'month_day', 'evidence': ['E1']},
                          'inferred_event_date': None}], 'uncertainties': ['原文未给出年份，未推断为发布日期年份。']}
    good = {'case_id': 'SYNTHETIC_REVIEW', 'prompt': prompt, 'answer': answer, 'source_context': full}
    bad = copy.deepcopy(good)
    bad['case_id'] = 'SYNTHETIC_BLOCKED'
    bad['answer']['source_statement'][0]['quote'] = '甲公司项目A于8月8日正式投产。'
    bad['answer']['answer'] = '甲公司项目A已正式投产。'
    bad['answer']['claims'][0]['event_state'] = 'event_reported_completed'
    return [good, bad]


def run(cases, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, case in enumerate(cases):
        # Numeric artifact names keep untrusted case IDs out of filesystem paths.
        folder = output / 'attempts' / str(index)
        folder.mkdir(parents=True, exist_ok=True)
        payload = {'model': 'synthetic-fixture-not-a-model', 'prompt': json.dumps(case['prompt'], ensure_ascii=False)}
        native = {'model': payload['model'], 'response': json.dumps(case['answer'], ensure_ascii=False),
                  'done': True, 'done_reason': 'stop'}
        outcome = {'body_bytes': json.dumps(native, ensure_ascii=False).encode('utf-8'),
                   'http_status': 200, 'response_headers': {'Content-Type': 'application/json'},
                   'transport_error': None, 'post_started': False,
                   'offline_envelope_reconstructed_from_supplied_answer': True,
                   'actual_cloud_calls': 0}
        row = collect_v04(outcome, folder, payload, 'formal', 'public_offline',
                          mode='supplied_answer_offline_validation', validation_context=case.get('source_context'))
        row.update(case_id=case['case_id'], question=case['prompt']['question'],
                   original_answer_unchanged=row.get('answer_object') == case['answer'])
        rows.append(row)
    result = {'mode': 'synthetic_fixture' if all(str(c['case_id']).startswith('SYNTHETIC_') for c in cases)
                         else 'supplied_answer_offline_validation',
              'new_model_calls': 0, 'new_http_requests': 0,
              'not_a_model_quality_score': True, 'cases': rows}
    (output / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    write_results_page(result, output / 'results.html', 'RAG V2.0 · 离线问答审查演示')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, help='JSON array: case_id, prompt, answer, optional source_context')
    parser.add_argument('--output', type=Path, default=ROOT.parent / 'outputs/v2')
    args = parser.parse_args(argv)
    if args.input and args.input.resolve().is_relative_to(args.output.resolve()):
        parser.error('input must be outside output directory; preserve original evidence')
    cases = json.loads(args.input.read_text(encoding='utf-8-sig')) if args.input else synthetic_cases()
    if not isinstance(cases, list) or not cases:
        parser.error('input must be a nonempty array')
    result = run(cases, args.output)
    print(json.dumps({'mode': result['mode'], 'model_calls': 0,
                      'statuses': {r['case_id']: r['whole_answer_trust'] for r in result['cases']},
                      'html': str(args.output / 'results.html')}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
