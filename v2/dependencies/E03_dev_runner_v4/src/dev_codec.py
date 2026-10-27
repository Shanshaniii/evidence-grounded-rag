"""Reuse native envelope validation/redaction; new dev answer/plan schema and business classification."""
import hashlib,json
from reviewed.codec import parse_native_response,strict_loads
from reviewed.unicode_utils import unicode_scalar_error
from redaction import safe_raw,scrub
from journal import durable_bytes
from tools import EvidenceTools
from display_safe import strip_reasoning,safe_response_representation
def schema_error(o,plan=False):
 if not isinstance(o,dict): return 'generated_object_required'
 if plan:
  if set(o)!={'needs_more_evidence','tool_calls','reason'} or type(o.get('needs_more_evidence')) is not bool or not isinstance(o.get('reason'),str) or not isinstance(o.get('tool_calls'),list) or len(o['tool_calls'])>3: return 'plan_schema_invalid'
  for c in o['tool_calls']:
   if not isinstance(c,dict) or set(c)!={'name','arguments'} or c.get('name') not in ['search_text','query_events','get_document','verify_citations','calculate_literal_sum'] or not isinstance(c.get('arguments'),dict): return 'plan_tool_schema_invalid'
  return None
 if set(o)!={'answer_status','answer','claims','uncertainties'} or o.get('answer_status') not in ['supported','insufficient_evidence','conflict'] or not isinstance(o.get('answer'),str) or not isinstance(o.get('claims'),list) or len(o['claims'])>12 or not isinstance(o.get('uncertainties'),list) or any(not isinstance(x,str) for x in o['uncertainties']): return 'answer_schema_invalid'
 for c in o['claims']:
  if not isinstance(c,dict) or set(c)!={'claim_id','text','subject','event_time','state','project_identity','citations'} or any(not isinstance(c.get(k),str) for k in ['claim_id','text','subject','event_time','state','project_identity']) or c['state'] not in ['planned','signed','reported_completed','conditional','historical','unknown','conflict'] or not isinstance(c.get('citations'),list) or not c['citations']: return 'claim_schema_invalid'
  for x in c['citations']:
   if not isinstance(x,dict) or x.get('kind') not in ['body','metadata'] or not isinstance(x.get('article_id'),str) or not isinstance(x.get('quote'),str): return 'citation_schema_invalid'
   if x['kind']=='body' and (set(x)!={'kind','article_id','start','end','quote'} or type(x.get('start')) is not int or type(x.get('end')) is not int): return 'citation_schema_invalid'
   if x['kind']=='metadata' and (set(x)!={'kind','article_id','field','quote'} or x.get('field')!='title'): return 'citation_schema_invalid'
 return None
def evidence_anchors(value):
 out=[]
 def walk(v):
  if isinstance(v,list):
   for x in v: walk(x)
  elif isinstance(v,dict):
   if isinstance(v.get('article_id'),str):
    if isinstance(v.get('body'),str): out.append({'article_id':v['article_id'],'start':0,'end':len(v['body']),'text':v['body'],'title':v.get('title')})
    elif type(v.get('start')) is int and type(v.get('end')) is int and isinstance(v.get('text',v.get('quote')),str): out.append({'article_id':v['article_id'],'start':v['start'],'end':v['end'],'text':v.get('text',v.get('quote')),'title':v.get('title')})
   for x in v.values(): walk(x)
 walk(value);return out
def cite_checks(obj,payload):
 corpus=EvidenceTools();source=corpus.verify_citations(obj.get('claims',[]));anchors=evidence_anchors(json.loads(payload['prompt']).get('evidence',[]));out=[];i=0
 for c in obj.get('claims',[]):
  for x in c['citations']:
   supplied=False
   for e in anchors:
    if e['article_id']!=x['article_id']: continue
    if x['kind']=='metadata': supplied=e.get('title')==x['quote']
    elif e['start']<=x['start']<x['end']<=e['end']: supplied=e['text'][x['start']-e['start']:x['end']-e['start']]==x['quote']
    if supplied: break
   out.append(source[i]|{'supplied_context_valid':supplied});i+=1
 return out
def collect(outcome,folder,payload,stage,arm,token='',mode='fixture',*,answer_validator=None):
 raw=outcome['body_bytes'];_,rep=safe_raw(raw,token);rep=safe_response_representation(rep)
 from reviewed.unicode_utils import safe_json
 safe=safe_json(rep,pretty=True).encode('utf-8');durable_bytes(folder/'response.safe.json',safe)
 r=strip_reasoning(scrub({k:v for k,v in outcome.items() if k!='body_bytes'},token))
 r.update(arm=arm,stage=stage,mode=mode,error_class='protocol_error',error_code=None,stop='none',backend_terminal=False,answer_object=None,usage=None,citation_checks=[],response_original_sha256=hashlib.sha256(raw).hexdigest(),response_original_bytes=len(raw),response_safe_sha256=hashlib.sha256(safe).hexdigest(),safe_raw=rep,original_bytes_persisted=False,reasoning_body_persisted=False,semantic_QA=None)
 err=outcome['transport_error']
 if err:
  r.update(error_class='transport_error',error_code=err,stop='pause' if outcome['post_started'] else 'none',backend_terminal=not outcome['post_started']);return r
 try: text=raw.decode('utf-8','strict')
 except UnicodeError: r.update(error_code='invalid_utf8',stop='pause');return r
 # Determine terminal state from original bytes, before recursively sanitizing logged values.
 try: native=strict_loads(text)
 except (ValueError,RecursionError): native=None
 terminal=isinstance(native,dict) and native.get('model')==payload['model'] and native.get('done') is True and native.get('done_reason') in ['stop','length']
 r['backend_terminal']=terminal
 decoded=parse_native_response(text,payload['model'],answer_validator=answer_validator or (lambda o:schema_error(o,stage=='plan')))
 r['native_codec_result']=strip_reasoning(scrub(decoded,token));r['usage']=decoded.get('usage')
 if not isinstance(native,dict):
  # Invalid/partial wire may contain unlabeled reasoning; retain diagnostics, not its text.
  for key in ['raw_response','raw_model_text']:
   if key in r['native_codec_result']: r['native_codec_result'][key]=None
 if token and (scrub(native,token)!=native or scrub(text,token)!=text):
  r.update(error_class='credential_leak',error_code='credential_in_response',stop='hard');return r
 if outcome['http_status']!=200 or (outcome['response_headers'].get('Content-Type') or '').split(';')[0].lower().strip()!='application/json':
  r.update(error_code='http_or_content_type_invalid',stop='none' if terminal else 'pause');return r
 usage=r['usage'] or {}
 if usage.get('prompt_eval_count',0)>8192 or usage.get('eval_count',0)>1000 or usage.get('prompt_eval_count',0)+1000>12288:
  r.update(error_class='budget_violation',error_code='observed_token_budget_exceeded',stop='hard');return r
 status=decoded['technical_status'];code=decoded.get('error_code')
 if status=='model_mismatch': r.update(error_class='data_anomaly',error_code=code,stop='hard');return r
 if status!='completed':
  business=status=='truncated' or code in ['generated_answer_json_invalid','generated_object_required','plan_schema_invalid','plan_tool_schema_invalid','answer_schema_invalid','claim_schema_invalid','citation_schema_invalid']
  r.update(error_class='business_error' if business else 'protocol_error',error_code=code,stop='none' if terminal else 'pause');return r
 obj=decoded['generated_object'];r['answer_object']=strip_reasoning(scrub(obj,token))
 if stage!='plan' and answer_validator is None:
  checks=cite_checks(obj,payload);r['citation_checks']=checks
  if any(not x['mechanical_valid'] or not x['supplied_context_valid'] for x in checks):
   r.update(error_class='business_error',error_code='citation_validation_failed');return r
  if obj['answer_status']=='supported' and not obj['claims']:
   r.update(error_class='business_error',error_code='supported_without_claims');return r
 r.update(error_class='completed',error_code=None,semantic_QA='pending_independent_QA');return r
