"""Keep the native envelope checks and report separate content/trust dimensions.

The historical transport, redaction, usage checks and safe raw persistence remain
in dev_codec. Its obsolete answer schema is explicitly replaced at this call site.
No model generation, semantic repair, gold access, or authorization happens here.
"""
from __future__ import annotations
import copy
from runtime_paths import configure_imports
configure_imports()
from dev_codec import collect
from reviewed.codec import strict_loads
from redaction import scrub
from display_safe import strip_reasoning
from diagnostics import evaluate


def _not_run(reason):
    return {'branches': {n: {'status': 'not_run', 'reason': reason, 'findings': []}
                         for n in ('structure','location','reference_binding','publication',
                                   'event_enum','time','conflict','free_text','source_identity')},
            'original_model_conclusion_changed': False,
            'whole_answer_trust': 'not_evaluated', 'confirmed': False}


def collect_v04(outcome, folder, payload, stage, arm, token='', mode='owned_fixture', *, validation_context=None):
    if stage == 'smoke':
        r = collect(outcome, folder, payload, stage, arm, token, mode)
        r.update(whole_answer_trust='not_evaluated', confirmed=False,
                 validation_status='not_run_smoke', technical_status=r.get('error_class'))
        return r
    r = collect(outcome, folder, payload, stage, arm, token, mode,
                answer_validator=lambda obj: None if isinstance(obj,dict) else 'v04_root_not_object')
    decoded = r.get('native_codec_result') or {}
    r['technical_status'] = decoded.get('technical_status', r['error_class'])
    r.update(whole_answer_trust='not_evaluated', confirmed=False, validation_status='not_run',
             validation_report=_not_run(r.get('error_code') or 'native_not_completed'),
             program_locations=[])
    if r['stop'] != 'none' or not r['backend_terminal']:
        return r
    if r.get('error_code') == 'v04_root_not_object':
        try:
            raw_obj = strip_reasoning(scrub(strict_loads(decoded['raw_model_text']),token))
        except (KeyError,ValueError,TypeError,RecursionError):
            return r
        report = evaluate(raw_obj,payload,source_context=validation_context)
        r.update(raw_model_object=raw_obj, canonical_object=report['canonical_object'],
                 validation_report=report, validation_status='failed',
                 whole_answer_trust='blocked', error_class='business_error')
        return r
    if r['error_class'] != 'completed' or decoded.get('technical_status') != 'completed':
        return r
    obj = copy.deepcopy(r['answer_object'])
    r['raw_model_object'] = copy.deepcopy(obj)
    try:
        report = evaluate(obj,payload,source_context=validation_context)
    except (OSError,ValueError,KeyError,TypeError,UnicodeError,RecursionError) as exc:
        # A local diagnosis failure quarantines this completed record. It does not
        # erase the response or claim semantic success, and does not retry a model.
        r.update(error_class='data_anomaly', error_code='diagnostic_input_or_source_integrity_failure',
                 validation_report=_not_run(type(exc).__name__), validation_status='not_evaluated',
                 whole_answer_trust='not_evaluated', stop='none')
        return r
    blocked = report['whole_answer_trust'] == 'blocked'
    r.update(canonical_object=report['canonical_object'], validation_report=report,
             program_locations=report['program_locations'],
             semantic_report=report.get('semantic_report'), semantic_QA='pending_independent_QA',
             whole_answer_trust=report['whole_answer_trust'],
             canonical_valid=report['canonical_valid'], narrative_valid=report['narrative_valid'],
             mechanical_valid=report['mechanical_valid'], answer_support=obj.get('answer_support'),
             validation_status='failed' if blocked else 'checks_passed_review_required',
             error_class='business_error' if blocked else 'completed',
             error_code='v04_structure_or_evidence_risks' if blocked else None)
    return r
