"""Portable export demonstration. Default offline; live is explicit and limited to one call."""
import pathlib,sys,json,copy,argparse,os,time,urllib.request,re
ROOT=pathlib.Path(__file__).resolve().parent;sys.path.insert(0,str(ROOT/'src'))
from contract import normalize,validate_structure,prompt_contract
from evidence_cards import select,dates
def request(case,model):
 cards,bounds,pool=select(case)
 sources={sid:{k:v for k,v in source.items() if k!='pub'} for sid,source in case['sources'].items()}
 prompt={'question':case['question'],'sources':sources,'cards':cards,'boundaries':bounds}
 rules='仅据证据卡答所问，100字内一个answer，不扩写无引用历史。source_id用card.src；quote逐字连续复制，正文与#title不混。冲突保留标题首末三层，不能确认实际完成。日期仅所引卡literal_dates，只有月日不补年；未知允许null。'+prompt_contract()
 return {'model':model,'system':rules,'prompt':json.dumps(prompt,ensure_ascii=False,separators=(',',':')),'stream':False,'think':False,'format':'json','options':{'temperature':0,'num_ctx':12288,'num_predict':1000}},cards
def diagnose(raw,case,cards):
 canonical,aliases,conflicts=normalize(raw);schema=validate_structure(canonical);risks=[];locations=[];table={}
 if not isinstance(canonical,dict):return {'canonical':canonical,'schema_errors':schema,'risks':['root_not_object'],'locations':[],'model_conclusion_changed':False}
 for s in canonical.get('source_statement',[]) if isinstance(canonical.get('source_statement'),list) else []:
  if not isinstance(s,dict):continue
  src=s.get('source_id');quote=s.get('quote');hits=[]
  if isinstance(src,str) and isinstance(quote,str) and quote:
   for card in cards:
    if card['src']!=src:continue
    start=0
    while (at:=card['text'].find(quote,start))>=0:hits.append((card['start']+at,card['start']+at+len(quote)));start=at+1
  hits=sorted(set(hits));locations.append({'statement_id':s.get('id'),'source_id':src,'spans':hits,'status':'unique' if len(hits)==1 else 'ambiguous' if hits else 'unresolved'})
  if len(hits)!=1:risks.append('quote_not_unique_literal_in_supplied_cards')
  if s.get('id') in table:risks.append('duplicate_statement_id')
  table[s.get('id')]=s
 for claim in canonical.get('claims',[]) if isinstance(canonical.get('claims'),list) else []:
  if not isinstance(claim,dict):continue
  refs=claim.get('source_statement',[])
  if not isinstance(refs,list) or any(x not in table for x in refs):risks.append('invalid_statement_reference')
  date=claim.get('explicit_event_date')
  if isinstance(date,dict) and date.get('value') is not None:
   quoted=[table[x].get('quote','') for x in date.get('evidence',[]) if x in table];allowed=[pair for q in quoted for pair in dates(q)]
   if [date.get('precision'),date.get('value')] not in allowed:risks.append('event_date_not_literal_in_quote')
  if claim.get('event_state') in ['event_conflicting','event_uncertain','event_unknown']:
   answer=canonical.get('answer','')
   for clause in re.split(r'[。；，]',answer if isinstance(answer,str) else ''):
    if re.search(r'上线|开通|投用|交付|投产|完成',clause) and not re.search(r'不能|未决|冲突|未知|不确定|计划|将于|报道|标题|正文|可能',clause):risks.append('unqualified_event_under_uncertainty')
 return {'canonical':canonical,'schema_errors':schema,'alias_conflicts':conflicts,'risks':risks,'locations':locations,'model_conclusion_changed':False,'semantic_QA':'independent_review_required'}
def run_live(payload):
 base=os.environ.get('OLLAMA_BASE_URL');model=os.environ.get('OLLAMA_MODEL')
 if not base or not model:raise ValueError('Set OLLAMA_BASE_URL and OLLAMA_MODEL explicitly; there is no private default.')
 payload['model']=model;request_bytes=json.dumps(payload,ensure_ascii=False).encode();started=time.monotonic()
 with urllib.request.urlopen(urllib.request.Request(base.rstrip('/')+'/api/generate',data=request_bytes,headers={'Content-Type':'application/json'}),timeout=180) as r:result=json.load(r)
 if time.monotonic()-started>180:raise TimeoutError('absolute deadline exceeded; no retry, confirm backend termination before another request')
 if result.get('done') is not True:raise ValueError('backend termination unconfirmed; no automatic retry')
 return json.loads(result['response']),{k:result.get(k) for k in ['done','done_reason','prompt_eval_count','eval_count','total_duration','load_duration','prompt_eval_duration','eval_duration']}
def main():
 p=argparse.ArgumentParser();p.add_argument('--case',type=pathlib.Path,default=ROOT/'examples/synthetic_case.json');p.add_argument('--response',type=pathlib.Path,default=ROOT/'examples/synthetic_response.json');p.add_argument('--live',action='store_true');p.add_argument('--output',type=pathlib.Path);args=p.parse_args()
 case=json.loads(args.case.read_text('utf-8'));payload,cards=request(case,os.environ.get('OLLAMA_MODEL','explicit_configuration_required'))
 if args.live:raw,usage=run_live(payload)
 else:raw=json.loads(args.response.read_text('utf-8'));usage=None
 before=copy.deepcopy(raw);report=diagnose(raw,case,cards);assert before==raw
 out={'mode':'real_single_call' if args.live else 'synthetic_fixture_no_model_call','raw_model_or_fixture_object':raw,'program_source_metadata':case['sources'],'program_diagnostics':report,'usage':usage,'new_real_requests':1 if args.live else 0,'publication_is_not_event_date':True}
 text=json.dumps(out,ensure_ascii=False,indent=2)
 if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(text,'utf-8')
 else:print(text)
if __name__=='__main__':main()
