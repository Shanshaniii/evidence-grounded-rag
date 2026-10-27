'Only frozen-public corpus/events tools; no QA reference or remote model access.'
import pathlib,json,re,math,collections,time,decimal
ROOT=pathlib.Path(__file__).resolve().parents[1]
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def terms(s):
 out=[]
 for word in re.findall(r'[\u4e00-\u9fff]+|[A-Za-z0-9]+',s.lower()):out.extend([word[i:i+2] for i in range(len(word)-1)] if len(word)>1 and re.match(r'[\u4e00-\u9fff]',word) else [word])
 return out
class EvidenceTools:
 def __init__(self):
  self.corpus=read(ROOT/'data/corpus.json');self.docs={a['article_id']:a for a in self.corpus};self.events=read(ROOT/'data/events.json');self.chunks=[]
  for a in self.corpus:
   paragraphs=list(re.finditer(r'[^\n]+',a['body']))
   for i,m in enumerate(paragraphs):
    start=paragraphs[max(0,i-3)].start();end=paragraphs[min(len(paragraphs)-1,i+1)].end();text=a['body'][start:end];self.chunks.append({'article_id':a['article_id'],'start':start,'end':end,'text':text,'tf':collections.Counter(terms(text))})
  self.df=collections.Counter(t for c in self.chunks for t in c['tf'])
 def search_text(self,query,k=3,max_chars=3600):
  if not isinstance(query,str) or not 1<=len(query)<=500 or type(k) is not int or not 1<=k<=3 or type(max_chars) is not int or not 500<=max_chars<=3600:raise ValueError('search args invalid')
  qt=set(terms(query));rank=[];N=len(self.chunks)
  for c in self.chunks:
   score=sum(math.log(1+(N-self.df[t]+.5)/(self.df[t]+.5))*min(c['tf'][t],2) for t in qt if c['tf'][t])
   if score:rank.append((score,c))
  out=[];used=0
  for score,c in sorted(rank,key=lambda x:(-x[0],x[1]['article_id'],x[1]['start'])):
   if any(c['article_id']==x['article_id'] and min(c['end'],x['end'])>max(c['start'],x['start']) for x in out):continue
   if len(c['text'])+used>max_chars:continue
   a=self.docs[c['article_id']];out.append({'evidence_id':a['news_id']+'@'+str(c['start'])+':'+str(c['end']),'article_id':a['article_id'],'title':a['title'],'url':a['url'],'directory_company_not_subject':a['company'],'published_at_raw':a['published_at_raw'],'timezone':'unspecified','start':c['start'],'end':c['end'],'text':c['text'],'source_scope':'report_only','score':round(score,4)});used+=len(c['text'])
   if len(out)==k:break
  return out
 def query_events(self,query,limit=5):
  if not isinstance(query,str) or not 1<=len(query)<=500 or type(limit) is not int or not 1<=limit<=5:raise ValueError('event args invalid')
  qt=set(terms(query));rank=[]
  for e in self.events:
   text=e['subject']+' '+e['event_kind']+' '+e['event_time']+' '+e['project_identity']+' '+e['evidence'][0]['quote'];score=len(qt&set(terms(text)))
   if score:rank.append((score,e))
  return [e for score,e in sorted(rank,key=lambda x:(-x[0],x[1]['event_id']))[:limit]]
 def get_document(self,article_id):
  if not isinstance(article_id,str) or article_id not in self.docs:raise ValueError('unknown source')
  a=self.docs[article_id];return {k:a[k] for k in ['article_id','title','url','published_at_raw','body','body_sha256']}
 def verify_citations(self,claims):
  if not isinstance(claims,list) or len(claims)>12:raise ValueError('claims invalid')
  out=[]
  for c in claims:
   if not isinstance(c,dict):out.append({'valid':False,'reason':'claim_not_object'});continue
   for x in c.get('citations',[]) if isinstance(c.get('citations'),list) else [None]:
    valid=False;reason='citation_invalid'
    if isinstance(x,dict):
     a=self.docs.get(x.get('article_id'))
     if a and x.get('kind')=='metadata':valid=x.get('field')=='title' and x.get('quote')==a['title']
     elif a and x.get('kind')=='body':
      s,e=x.get('start'),x.get('end');q=x.get('quote');valid=type(s) is int and type(e) is int and isinstance(q,str) and 0<=s<e<=len(a['body']) and a['body'][s:e]==q and e-s==len(q)
    out.append({'claim_id':c.get('claim_id'),'mechanical_valid':valid,'semantic_support':'pending_QA','no_repair':True})
  return out
 def calculate_literal_sum(self,event_ids,field):
  # Deterministic Decimal over explicit source literals, no arbitrary expression or mixed currency/status.
  if not isinstance(event_ids,list) or not 1<=len(event_ids)<=3 or len(set(event_ids))!=len(event_ids) or not isinstance(field,str):raise ValueError('sum args invalid')
  values=[];units=set();states=set();refs=[]
  for eid in event_ids:
   ev=next((e for e in self.events if e['event_id']==eid),None)
   if ev is None or field not in ev['literal_values']:return {'status':'missing','value':None,'reason':'literal field absent'}
   text=ev['literal_values'][field];m=re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)(亿|万)?(欧元|元人民币|亿元人民币|万元人民币|亩)',text)
   if not m:return {'status':'unsupported','value':None,'reason':'unit literal not safely parsed'}
   factor=decimal.Decimal('100000000') if m[2]=='亿' else decimal.Decimal('10000') if m[2]=='万' else decimal.Decimal(1);values.append(decimal.Decimal(m[1])*factor);units.add(m[3]);states.add(ev['modality']);refs+=ev['evidence']
  if len(units)!=1 or len(states)!=1:return {'status':'incompatible','value':None,'reason':'mixed units/states'}
  return {'status':'computed_literal_only','value':str(sum(values)),'unit':next(iter(units)),'source_state':next(iter(states)),'source_refs':refs,'scope':'not actual payment or finance metric'}
 def call(self,name,args):
  methods={'search_text':self.search_text,'query_events':self.query_events,'get_document':self.get_document,'verify_citations':self.verify_citations,'calculate_literal_sum':self.calculate_literal_sum}
  if name not in methods or not isinstance(args,dict):raise ValueError('tool not allowed')
  start=time.monotonic();value=methods[name](**args);return {'tool':name,'arguments':args,'result':value,'local_tool_elapsed_ms':round((time.monotonic()-start)*1000,3),'mode':'deterministic_read_only','model_selected_tool':False}
