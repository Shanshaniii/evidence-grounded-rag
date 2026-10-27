"""Exact, Unicode-codepoint source quote locator. No normalization, inference, span guesses or repairs."""
import json,hashlib,pathlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
P2=ROOT.parents[1]/'experiments/E02_P2_D2_v2'
def documents():
 docs=json.loads((P2/'data/corpus.json').read_text(encoding='utf-8'))
 for d in docs:
  if hashlib.sha256(d['body'].encode('utf-8')).hexdigest()!=d['body_sha256']:raise ValueError('source_body_hash_mismatch')
 return {d['article_id']:d for d in docs}
def locate(source_id,quote,docs,aliases=None,supplied=None,max_chars=None):
 if not isinstance(source_id,str):return {'source_id':source_id,'quote':quote,'status':'unresolved','reason':'unknown_or_invalid_source','candidates':[],'selected_span':None,'supplied_context_valid':None,'semantic_support':'pending_independent_QA','conclusion_changed':False}
 aliases=aliases or {};title=isinstance(source_id,str) and source_id.endswith('#title');key=source_id[:-6] if title else source_id;aid=aliases.get(key,key);doc=docs.get(aid) if isinstance(aid,str) else None
 out={'source_id':source_id,'quote':quote,'status':'unresolved','reason':None,'candidates':[],'selected_span':None,'supplied_context_valid':None,'semantic_support':'pending_independent_QA','conclusion_changed':False,'matching':'exact; no normalization; codepoint indices'}
 if doc is None:out['reason']='unknown_source';return out
 if hashlib.sha256(doc['body'].encode('utf-8')).hexdigest()!=doc['body_sha256']:raise ValueError('source_body_hash_mismatch')
 out['article_id']=aid;out['source_body_sha256']=doc['body_sha256']
 out['source_title_sha256']=hashlib.sha256(doc['title'].encode('utf-8')).hexdigest()
 if not isinstance(quote,str) or not quote:out['reason']='empty_or_nonstring_quote';return out
 if max_chars is not None and len(quote)>max_chars:out['reason']='quote_length_contract_exceeded';return out
 if title:
  if quote in doc['title']:
   pos=0
   while True:
    pos=doc['title'].find(quote,pos)
    if pos<0:break
    out['candidates'].append({'kind':'metadata','field':'title','article_id':aid,'start':pos,'end':pos+len(quote),'quote':quote});pos+=1
 else:
  start=0
  while True:
   pos=doc['body'].find(quote,start)
   if pos<0:break
   end=pos+len(quote);assert doc['body'][pos:end]==quote
   out['candidates'].append({'kind':'body','article_id':aid,'start':pos,'end':end,'quote':quote});start=pos+1
 if not out['candidates']:out['reason']='no_exact_match';return out
 if len(out['candidates'])>1:out.update(status='ambiguous',reason='multiple_exact_matches');return out
 out.update(status='unique',reason=None,selected_span=out['candidates'][0])
 if supplied is not None:
  span=out['selected_span'];out['supplied_context_valid']=any(e.get('article_id')==aid and (e.get('title')==quote if title else type(e.get('start')) is int and e['start']<=span['start']<span['end']<=e['end'] and e['text'][span['start']-e['start']:span['end']-e['start']]==quote) for e in supplied)
 return out
def locate_claims(obj,payload,docs=None):
 docs=docs or documents();body=json.loads(payload['prompt']);sources=body.get('sources',{});aliases={k:v['article_id'] for k,v in sources.items()};supplied=[e|{'article_id':sources[e['src']]['article_id'],'title':sources[e['src']]['title']} for e in body['evidence']];rows=[]
 for claim in obj.get('claims',[]):
  for i,c in enumerate(claim['citations']):
   sid=c['source_id'];key=sid[:-6] if sid.endswith('#title') else sid
   location=locate(sid,c['quote'],docs,aliases,supplied,64) if key in aliases else {'source_id':sid,'quote':c['quote'],'status':'unresolved','reason':'source_id_not_in_current_input_catalog','candidates':[],'selected_span':None,'supplied_context_valid':False,'semantic_support':'pending_independent_QA','conclusion_changed':False}
   rows.append({'claim_id':claim['claim_id'],'citation_index':i,'model_claim_text':claim['text'],'location':location})
 return rows
def answer_error(o):
 if not isinstance(o,dict) or set(o)!={'answer_status','answer','claims','uncertainties'} or o.get('answer_status') not in ['supported','insufficient_evidence','conflict'] or not isinstance(o.get('answer'),str) or not isinstance(o.get('claims'),list) or len(o['claims'])>12 or not isinstance(o.get('uncertainties'),list) or any(not isinstance(x,str) for x in o['uncertainties']):return 'short_quote_answer_schema_invalid'
 for c in o['claims']:
  if not isinstance(c,dict) or set(c)!={'claim_id','text','subject','event_time','state','project_identity','citations'} or any(not isinstance(c.get(k),str) for k in ['claim_id','text','subject','event_time','state','project_identity']) or c['state'] not in ['planned','signed','reported_completed','conditional','historical','unknown','conflict'] or not isinstance(c.get('citations'),list) or not c['citations']:return 'short_quote_claim_schema_invalid'
  for x in c['citations']:
   if not isinstance(x,dict) or set(x)!={'source_id','quote'} or not isinstance(x['source_id'],str) or not isinstance(x['quote'],str) or not 1<=len(x['quote'])<=64:return 'short_quote_citation_contract_invalid'
 return None


def locate_supplied(source_id, quote, sources, cards, full_registry=None, max_chars=64, publication=None):
 """Exact quotation in supplied cards, with honest local/global coordinate scope.

 The uploaded registry can contain gaps and redacted, length-changing strings.
 It is never padded into a fabricated document. A global coordinate is reported
 only when the corresponding supplied registry slice verifies it exactly.
 """
 title=isinstance(source_id,str) and source_id.endswith('#title')
 pub=isinstance(source_id,str) and source_id.endswith('#publication')
 base=source_id[:-12] if pub else source_id[:-6] if title else source_id
 out={'source_id':source_id,'quote':quote,'status':'unresolved','reason':None,
      'candidates':[],'selected_span':None,'supplied_context_valid':False,
      'semantic_support':'pending_independent_QA','conclusion_changed':False,
      'matching':'continuous exact Unicode codepoint substring; no normalization'}
 if not isinstance(base,str) or base not in sources:
  out['reason']='unknown_input_source';return out
 meta=sources[base] if isinstance(sources[base],dict) else {}
 out['article_id']=meta.get('article_id',meta.get('id'))
 out['source_alias']=base
 if not isinstance(quote,str) or not quote:
  out['reason']='empty_or_nonstring_quote';return out
 # Length is a contract property, not a reason to stop locating. A quotation can
 # violate the length limit and still be verbatim in the supplied source;
 # returning here would silently turn a length defect into a not_literal verdict.
 length_exceeded = max_chars is not None and len(quote) > max_chars
 out['quote_chars']=len(quote)
 out['quote_limit']=max_chars
 out['quote_length_contract_exceeded']=bool(length_exceeded)
 if pub:
  supplied=(publication or {}).get(base,{}) if isinstance(publication,dict) else {}
  value=supplied.get('published_at') if isinstance(supplied,dict) else None
  if isinstance(value,str) and quote==value:
   span={'kind':'metadata','field':'published_at','source_id':source_id,'quote':quote,'coordinate_scope':'supplied_publication_metadata','origin':supplied.get('origin'),'source_coordinate_verified':True}
   out.update(status='unique',candidates=[span],selected_span=span,supplied_context_valid=True,source_level_provenance_unique=True,original_document_verified=False)
  else:out['reason']='publication_quote_not_exact_supplied_metadata'
  return out
 candidates=[];seen=set()
 for index,card in enumerate(cards if isinstance(cards,list) else []):
  if not isinstance(card,dict) or card.get('src')!=source_id or not isinstance(card.get('text'),str):continue
  text=card['text'];pos=0
  while True:
   pos=text.find(quote,pos)
   if pos<0:break
   local_end=pos+len(quote)
   span={'source_id':source_id,'article_id':out['article_id'],
         'kind':'metadata' if title else 'body','field':'title' if title else 'body',
         'quote':quote,'card_id':card.get('id'),'card_index':index,
         'local_start':pos,'local_end':local_end,'coordinate_scope':'supplied_card',
         'source_excerpt_sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),
         'declared_start':card.get('start'),'declared_end':card.get('end'),
         'source_coordinate_verified':False}
   if title and isinstance(meta.get('title'),str) and text==meta['title']:
    span.update(start=pos,end=local_end,coordinate_scope='provided_title',source_coordinate_verified=True)
   elif not title and type(card.get('start')) is int and type(card.get('end')) is int and card['end']-card['start']==len(text):
    for row in full_registry or []:
     if not isinstance(row,(list,tuple)) or len(row)!=4:continue
     sid,start,end,original=row
     if sid!=base or type(start) is not int or type(end) is not int or not isinstance(original,str) or end-start!=len(original):continue
     left=card['start']-start;right=card['end']-start
     if 0<=left<=right<=len(original) and original[left:right]==text:
      span.update(start=card['start']+pos,end=card['start']+local_end,
                  coordinate_scope='provided_source_registry',source_coordinate_verified=True)
      break
   key=(source_id,span.get('start'),span.get('end')) if span['source_coordinate_verified'] else (source_id,index,pos,local_end)
   if key not in seen:candidates.append(span);seen.add(key)
   pos+=1
 out['candidates']=candidates
 # Independent of the supplied-card scope: is this text verbatim anywhere in the
 # provided source registry? This keeps "not in one supplied card" distinct from
 # "not literal at all", which are different defects.
 out['verbatim_in_provided_source_registry']=None
 if full_registry and isinstance(full_registry,(list,tuple)):
  out['verbatim_in_provided_source_registry']=any(
   isinstance(r,(list,tuple)) and len(r)==4 and isinstance(r[0],str) and r[0]==base
   and isinstance(r[3],str) and quote in r[3] for r in full_registry)
 if not candidates:
  out['reason']=('quote_length_contract_exceeded_and_not_in_supplied_source_cards'
                 if out['quote_length_contract_exceeded'] else 'quote_not_in_supplied_source_cards');return out
 out['supplied_context_valid']=True
 if len(candidates)==1:
  out.update(status='unique',selected_span=candidates[0])
 else:
  out.update(status='ambiguous',reason='multiple_exact_matches_in_supplied_source')
 out['source_level_provenance_unique']=True
 out['original_document_verified']=False
 return out
