import json,re
from reviewed.unicode_utils import unicode_scalar_error,safe_diagnostics,safe_json,escape_non_scalars
from reviewed.validators import answer_schema_error
PROTOCOL='ollama_generate'
USAGE_KEYS=['total_duration','load_duration','prompt_eval_count','prompt_eval_duration','prompt_eval_cached_count','eval_count','eval_duration']
def reject_nonfinite(value):raise ValueError('Non-finite JSON constant')

def _unique_object(pairs):
 result={}
 for key,value in pairs:
  if key in result:raise ValueError('duplicate_JSON_key:'+key)
  result[key]=value
 return result

def strict_loads(text):return json.loads(text,parse_constant=reject_nonfinite,object_pairs_hook=_unique_object)

def parse_native_response(raw,expected_model=None,credential='',answer_validator=answer_schema_error):
 result={'protocol':PROTOCOL,'technical_status':'invalid_response','answer_status':None,'answer':None,'claims':None,'missing_evidence':None,'semantic_support':'not_run','raw_response':None,'raw_model_text':None,'usage':None,'actual_cloud_calls':0}
 def fail(code,status='invalid_response'):return safe_diagnostics(result)|{'technical_status':status,'error_code':code}
 if not isinstance(raw,str):return fail('raw_response_not_text')
 text=raw.replace(credential,'[REDACTED]') if credential else raw;result['raw_response']=escape_non_scalars(text)
 if unicode_scalar_error(text):return fail('raw_non_scalar_unicode')
 try:o=strict_loads(text)
 except (ValueError,TypeError,RecursionError):return fail('single_native_json_object_required')
 if not isinstance(o,dict) or unicode_scalar_error(o):return fail('native_object_or_unicode_invalid')
 if 'error' in o:
  if not isinstance(o['error'],str) or not o['error']:return fail('native_error_field_invalid')
  result['server_error_text']=o['error'];return fail('native_server_error','server_error')
 if any(k in o for k in ['choices','message','messages']):return fail('foreign_chat_or_openai_schema_rejected')
 if not isinstance(o.get('model'),str) or not o['model'] or not isinstance(o.get('response'),str) or type(o.get('done')) is not bool or not isinstance(o.get('done_reason'),str):return fail('native_response_schema_invalid')
 if expected_model is not None and o['model']!=expected_model:return fail('response_model_mismatch','model_mismatch')
 result.update({'response_model':o['model'],'native_done':o['done'],'done_reason':o['done_reason'],'raw_model_text':o['response'],'created_at':o.get('created_at')})
 if 'created_at' in o and not isinstance(o['created_at'],str):return fail('created_at_not_text')
 for key in USAGE_KEYS:
  if key in o and (type(o[key]) is not int or not 0<=o[key]<=2**63-1):return fail('usage_count_or_nanoseconds_invalid')
 usage={k:o[k] for k in USAGE_KEYS if k in o};usage['duration_unit']='nanoseconds';usage['token_per_second']=o.get('eval_count',0)*1e9/o['eval_duration'] if o.get('eval_duration',0)>0 and 'eval_count' in o else None;result['usage']=usage
 if not o['done']:return fail('native_generation_incomplete','incomplete_response')
 if o['done_reason']=='length':return fail('native_output_limit','truncated')
 if o['done_reason']!='stop':return fail('native_terminal_reason_not_stop','failed')
 if 'thinking' in o and o['thinking'] is not None and not isinstance(o['thinking'],str):return fail('thinking_field_invalid')
 if isinstance(o.get('thinking'),str) and o['thinking'].strip() or re.search(r'<\s*/?\s*think\s*>',o['response'],flags=re.I):return fail('think_false_policy_unverified_or_violated','thinking_policy_violation')
 try:a=strict_loads(o['response'])
 except (ValueError,TypeError,RecursionError):return fail('generated_answer_json_invalid')
 error=answer_validator(a)
 if error:return fail(error)
 return safe_diagnostics(result)|{'technical_status':'completed','generated_object':a,'answer_status':a.get('answer_status'),'answer':a.get('answer'),'claims':a.get('claims'),'missing_evidence':a.get('missing_evidence'),'semantic_support':'pending_independent_review'}
