"""Production diagnostics with independent structure, citation and semantic branches.

Uses the existing candidate checker and historical exact locator. Model output is
never repaired; supplied-card evidence remains separate from program audit data.
"""
from __future__ import annotations
import copy
import json
import re
from pathlib import Path
from runtime_paths import configure_imports, load_dependency, PRODUCTION_ROOT
configure_imports()
from contract import normalize, validate_structure, SCHEMA
from checker_v2 import evaluate_model_answer
from reviewed.codec import strict_loads
ROOT=PRODUCTION_ROOT
locator=load_dependency('rag_supplied_source_locator','E03_RAG_L_v1/src/locator.py')


def context(payload, source_context=None):
 b=copy.deepcopy(payload['prompt']) if isinstance(payload.get('prompt'),dict) else strict_loads(payload['prompt'])
 if not isinstance(b,dict) or not isinstance(b.get('sources'),dict):raise ValueError('source_projection_not_object')
 sources=copy.deepcopy(b['sources']);cards=b.get('cards')
 if cards is None:
  cards=[]
  for i,row in enumerate(b.get('registry',[])):
   if isinstance(row,(list,tuple)) and len(row)==4:
    cards.append({'id':'R'+str(i),'src':row[0],'start':row[1],'end':row[2],'text':row[3],'role':'body'})
  for i,row in enumerate(b.get('evidence',[])):
   if isinstance(row,dict):cards.append(dict(row,id=row.get('id','E'+str(i))))
  for sid,meta in sources.items():
   if isinstance(meta,dict) and isinstance(meta.get('title'),str):
    cards.append({'id':sid+'_TITLE','src':sid+'#title','text':meta['title'],'start':0,'end':len(meta['title']),'role':'title'})
 if not isinstance(cards,list):raise ValueError('cards_not_array')
 b['cards']=cards
 b['registry']=[[c['src'],c.get('start'),c.get('end'),c['text']] for c in cards if isinstance(c,dict) and isinstance(c.get('src'),str) and not c['src'].endswith('#title') and isinstance(c.get('text'),str)]
 return b,sources,cards


def _program_full(source_context):
 if not isinstance(source_context,dict):return None
 full=source_context.get('full_original_provided_input',source_context)
 return full if isinstance(full,dict) and isinstance(full.get('registry'),list) else None


def branch(status='executed',reason=None,findings=None):return {'status':status,'reason':reason,'findings':findings or []}
PLANNED_MARK=r'将于|拟于|预计|计划|即将|有望|拟'
DONE_MARK=r'已完成|正式上线|竣工投产|正式交付|成功交付|顺利出机|已经|圆满'
def date_values(card):
 # Cards carry literal_dates as [precision, value] pairs, not bare strings.
 ld=card.get('literal_dates')
 if not isinstance(ld,list):return set()
 return {x[1] for x in ld if isinstance(x,(list,tuple)) and len(x)>1 and isinstance(x[1],str)}
def layer_disagreement(cards,base,quotes):
 """True when a source's title layer contradicts the body layers a claim actually cites.

 System obligation (1) is conditional: the three layers must each be cited only when the
 title and the cited body passages disagree. Whether they disagree is decidable from the
 frozen cards themselves - compare literal dates and planned-versus-completed markers - so
 the check follows the contract instead of demanding a title citation for every source.
 """
 buckets={}
 for c in cards:
  if not isinstance(c,dict):continue
  src=c.get('src')
  if not isinstance(src,str):continue
  key=src[:-6] if src.endswith('#title') else src
  slot=buckets.setdefault(key,{'title':None,'body':[]})
  if src.endswith('#title'):slot['title']=c
  else:slot['body'].append(c)
 entry=buckets.get(base) or {}
 title=entry.get('title')
 if not title:return False
 ttext=title.get('text') or ''
 tplanned=bool(re.search(PLANNED_MARK,ttext));tdone=bool(re.search(DONE_MARK,ttext))
 tdates=date_values(title)
 if not (tplanned or tdone) and not tdates:return False # title carries no event information at all
 for quote in quotes:
  if not isinstance(quote,str) or not quote:continue
  for body in entry.get('body') or []:
   btext=body.get('text') or ''
   if quote not in btext:continue
   bplanned=bool(re.search(PLANNED_MARK,btext));bdone=bool(re.search(DONE_MARK,btext))
   if (tplanned and bdone and not bplanned) or (bplanned and tdone and not tplanned):return True
   bdates=date_values(body)
   if tdates and bdates:
    # A month-day in the title and a full date in the body describe the same day, not a conflict.
    tail=lambda s:{x[-5:] if re.fullmatch(r'\d{4}-\d{2}-\d{2}',x) else x for x in s}
    if not (tail(tdates)&tail(bdates)):return True
 return False
def safe_list(v):return v if isinstance(v,list) else None
def full_dates(text):return [(m.group(),f'{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}',m.start(),m.end()) for m in re.finditer(r'(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})(?:日)?',text)]
def clause(text,start,end):
 left=max([text.rfind(x,0,start) for x in ['。','，',',',';','；','\n']]+[-1])+1;right=min([i for x in ['。','，',',',';','；','\n'] if (i:=text.find(x,end))>=0]+[len(text)]);return text[left:right]
def strong_completion(text):
 for m in re.finditer(r'已完成|正式上线|竣工投产|正式交付|完成状态',text):
  part=clause(text,m.start(),m.end())
  if re.search(r'不能|无法|不确认|未能|尚未|未知|不确定|冲突|待证',part):continue
  if re.search(r'将于|拟于|预计|计划|即将|有望',part) and not re.search(r'已(?:经)?(?:完成|上线)|确定已|实际已',part):continue # quoted future reporting is not actual completion
  if re.search(r'标题|首段|末段|措辞',part) and not re.search(r'能确认|整体|确定|佐证|因此',part):continue
  return True
 return False
def unqualified_event_clauses(text):
 # Scope-limited consistency check, not general semantic QA. Work per clause so
 # an uncertainty sentence elsewhere cannot qualify an affirmative claim.
 if not isinstance(text,str):return []
 bad=[];previous=''
 actions=r'上线|开通|投用|交付|投产|收购|开工|签约|推出|完成|发生'
 qualifiers=r'不能|无法|不确认|未能|尚未|未知|不确定|冲突|待证|未决|未确认|未证明|不证明|不代表|不等于|计划|将于|拟(?:于)?|预计|即将|有望|可能|推断|推测|假设|报道|标题|正文|首段|末段|原文|记载|声称|称|说法'
 for part in re.split(r'[。；;！？!?，,\n]',text):
  if re.search(actions,part) and not re.search(qualifiers,part):
   # Reporting prefix split by a comma remains a limited reported proposition.
   if not re.search(r'(?:报道|标题|正文|原文|首段|末段).*(?:称|写道|表示|记载)[：:]?$',previous):bad.append(part)
  previous=part
 return bad


def evaluate(raw,payload,source_context=None):
 obj,changes,conflicts=normalize(raw);errors=validate_structure(obj)
 out={'canonical_object':obj,'normalization_records':changes,'alias_conflicts':conflicts,
      'raw_schema_errors':validate_structure(raw),'schema_errors':errors,'branches':{},
      'program_locations':[],'risk_findings':[],'warnings':[],
      'original_model_conclusion_changed':False,'semantic_QA':'pending_independent_QA',
      'whole_answer_trust':'blocked','confirmed':False,
      'canonical_valid':False,'narrative_valid':False,'mechanical_valid':False,
      'checker_version':'rag20_integrated_diagnostics_v1'}
 out['branches']['structure']=branch(findings=errors)
 names=['location','reference_binding','publication','event_enum','time','conflict','free_text','source_identity','question_coverage']
 if not isinstance(obj,dict):
  for name in names:out['branches'][name]=branch('not_run','root_not_object')
  return out
 try:b,sources,cards=context(payload,source_context)
 except (ValueError,KeyError,TypeError,UnicodeError,RecursionError) as exc:
  out['risk_findings'].append({'code':'validation_source_projection_invalid','locus':'canonical_object','severity':'blocking','detail':type(exc).__name__})
  for name in names:out['branches'][name]=branch('not_run','source_projection_invalid')
  return out
 full=_program_full(source_context);full_registry=full.get('registry',[]) if full else []
 stmts=obj.get('source_statement');table={};duplicate_ids=set();loc_findings=[]
 if isinstance(stmts,list):
  for i,s in enumerate(stmts):
   if not isinstance(s,dict):
    loc_findings.append({'index':i,'status':'not_run','reason':'statement_not_object'});continue
   loc=locator.locate_supplied(s.get('source_id'),s.get('quote'),sources,cards,full_registry,
                              SCHEMA['$defs']['statement']['properties']['quote']['maxLength'],publication=b.get('publication',{}))
   row={'statement_index':i,'statement_id':s.get('id'),'statement_original':copy.deepcopy(s),'location':loc}
   out['program_locations'].append(row)
   if bool(loc.get('quote_length_contract_exceeded')):
    out['warnings'].append({'code':'quote_exceeds_contract_length','index':i,'locus':'canonical_object','severity':'advisory','quote_chars':loc.get('quote_chars'),'quote_limit':loc.get('quote_limit'),'matched_in_supplied_source':bool(loc.get('supplied_context_valid')),'reason':loc['reason'],'note':'length recorded separately from literal match, position and support'})
   if not loc['supplied_context_valid']:
    if loc.get('verbatim_in_provided_source_registry') is True:
     out['risk_findings'].append({'code':'quote_not_within_single_supplied_card','index':i,'locus':'canonical_object','severity':'blocking','reason':loc['reason'],'quote_chars':loc.get('quote_chars'),'quote_limit':loc.get('quote_limit'),'note':'verbatim in the provided source but not continuous within one supplied card; reported as a scope contract violation, not as a literal or forgery failure'})
    else:
     out['risk_findings'].append({'code':'quote_not_grounded_in_supplied_source','index':i,'locus':'canonical_object','severity':'blocking','reason':loc['reason'],'quote_chars':loc.get('quote_chars'),'quote_limit':loc.get('quote_limit')})
   elif loc['status']=='ambiguous':
    out['warnings'].append({'code':'quote_occurrence_ambiguous','index':i,'locus':'canonical_object','severity':'advisory','candidate_count':len(loc['candidates']),'note':'source and text match; occurrence and context require review'})
   identifier=s.get('id')
   if isinstance(identifier,str):
    if identifier in table:duplicate_ids.add(identifier)
    else:table[identifier]=row
  for identifier in sorted(duplicate_ids):
   table.pop(identifier,None)
   out['risk_findings'].append({'code':'duplicate_statement_id_no_selection','statement_id':identifier,'locus':'canonical_object','severity':'blocking'})
  out['branches']['location']=branch(findings=loc_findings)
 else:out['branches']['location']=branch('not_run','source_statement_not_array')
 refs_findings=[]
 def refs(values,path,required=False):
  if not isinstance(values,list):
   refs_findings.append({'path':path,'status':'not_run','reason':'references_not_array'});return
  if required and not values:
   out['risk_findings'].append({'code':'statement_reference_required_nonempty','path':path,'locus':'canonical_object','severity':'blocking'})
  for i,value in enumerate(values):
   if not isinstance(value,str) or value not in table:
    out['risk_findings'].append({'code':'statement_reference_dangling_or_ambiguous','path':path,'index':i,'reference_original':value,'locus':'canonical_object','severity':'blocking'})
   else:refs_findings.append({'path':path,'index':i,'reference_original':value,'unique_statement_binding':True})
 claims=obj.get('claims')
 if isinstance(claims,list):
  for i,c in enumerate(claims):
   if not isinstance(c,dict):continue
   refs(c.get('source_statement'),f'claims[{i}].source_statement',True)
   for key in ('explicit_event_date','inferred_event_date'):
    value=c.get(key)
    if isinstance(value,dict):
     refs(value.get('evidence'),f'claims[{i}].{key}.evidence',value.get('value') is not None)
     if isinstance(c.get('source_statement'),list) and isinstance(value.get('evidence'),list):
      valid=set(x for x in c['source_statement'] if isinstance(x,str))
      if any(not isinstance(x,str) or x not in valid for x in value['evidence']):
       out['risk_findings'].append({'code':'date_evidence_outside_claim_references','claim':i,'locus':'canonical_object','severity':'blocking'})
  out['branches']['reference_binding']=branch(findings=refs_findings)
 else:out['branches']['reference_binding']=branch('not_run','claims_not_array')
 # Audit metadata can be displayed, but it is never inserted into supplied model evidence.
 metadata=(source_context or {}).get('source_metadata',{}) if isinstance(source_context,dict) else {}
 if isinstance(metadata,dict) and isinstance(metadata.get('source_metadata'),dict):metadata=metadata['source_metadata']
 out['program_source_metadata']=[{'source_id':sid,'title':meta.get('title'),'published_at_raw':meta.get('pub',meta.get('published_at_raw')),'origin':'provided_program_metadata','model_generated':False} for sid,meta in metadata.items() if isinstance(meta,dict)] if isinstance(metadata,dict) else []
 out['branches']['publication']=branch(findings=[{'supplied_publication':b.get('publication',b.get('publication_metadata',{})),'program_metadata_is_not_event_evidence':True}])
 semantic=evaluate_model_answer(obj,b,full=source_context if isinstance(source_context,dict) else full)
 out['semantic_report']=semantic
 for finding in semantic.get('findings',[]):
  if not isinstance(finding,dict):continue
  target='risk_findings' if finding.get('severity')=='blocking' else 'warnings'
  out[target].append(copy.deepcopy(finding))
 prefixes={'event_enum':('state','event','modality'), 'time':('date','time','publication'),
           'conflict':('conflict','counter','contrary'), 'free_text':('narrative','answer'),
           'source_identity':('subject','identity','project','entity'),
           'question_coverage':('input','question','coverage','evidence_supply')}
 for name,keys in prefixes.items():
  rows=[f for f in semantic.get('findings',[]) if isinstance(f,dict) and any(key in f.get('code','') for key in keys)]
  out['branches'][name]=branch(findings=rows)
 blockers=out['risk_findings']
 out['canonical_valid']=not errors and not conflicts and semantic.get('canonical_valid') is True and not any(f.get('locus')!='narrative_answer' for f in blockers)
 out['narrative_valid']=semantic.get('narrative_valid') is True and not any(f.get('locus')=='narrative_answer' for f in blockers)
 out['mechanical_valid']=out['canonical_valid']
 out['whole_answer_trust']='blocked' if errors or conflicts or blockers or not out['canonical_valid'] or not out['narrative_valid'] else 'review_required'
 out['answer_support']=obj.get('answer_support')
 out['semantic_scope']=semantic.get('semantic_scope','deterministic checks only; no general entailment proof')
 return out
