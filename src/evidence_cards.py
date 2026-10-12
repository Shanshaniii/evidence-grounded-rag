"""Deterministic source-local cards, derived only from frozen originally provided text."""
import re,math,collections
def dates(text):
 out=[]
 for m in re.finditer(r'(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})(?:日)?',text):out.append(['full_date',f'{int(m[1]):04}-{int(m[2]):02}-{int(m[3]):02}'])
 for m in re.finditer(r'(\d{4})年',text):out.append(['year',m[1]])
 for m in re.finditer(r'(\d{1,2})月(\d{1,2})日',text):out.append(['month_day',f'{int(m[1]):02}-{int(m[2]):02}'])
 return out
def terms(text):
 out=[]
 for run in re.findall(r'[\u4e00-\u9fff]+|[A-Za-z0-9]+',text):out.extend([run] if not re.search(r'[\u4e00-\u9fff]',run) else [run[i:i+2] for i in range(len(run)-1)])
 return set(out)
def select(b):
 pool=[]
 for sid,x in b['sources'].items():pool.append({'id':sid+'_TITLE','src':sid+'#title','start':0,'end':len(x['title']),'text':x['title'],'literal_dates':dates(x['title']),'role':'title'})
 for sid,start,end,text in b['registry']:
  for m in re.finditer(r'[^。！？\n]+[。！？]?|\n',text):
   for k in range(0,len(m.group()),64):
    part=m.group()[k:k+64]
    if part.strip():pool.append({'id':sid+'_P'+str(start+m.start()+k),'src':sid,'start':start+m.start()+k,'end':start+m.start()+k+len(part),'text':part,'literal_dates':dates(part),'role':'body'})
 qt=terms(b['question']);df=collections.Counter(t for x in pool for t in terms(x['text']));score=lambda x:sum(math.log((len(pool)+1)/(df[t]+1)) for t in terms(x['text'])&qt)
 core=[x for x in pool if x['role']=='title'];bounds=[]
 for sid,fs,fe,ls,le in b['boundaries']:
  picked=[]
  for lo,hi,role in [(fs,fe,'first'),(ls,le,'last')]:
   available=[x for x in pool if x['src']==sid and lo<=x['start']<hi]
   if not available:raise ValueError('no_original_boundary_card')
   card=sorted(available,key=lambda x:(-score(x),x['start']))[0].copy();card['role']=role;core.append(card);picked.append(card)
  bounds.append([sid,picked[0]['start'],picked[0]['end'],picked[1]['start'],picked[1]['end']])
 ranked=sorted(pool,key=lambda x:(-score(x),x['src'],x['start']));chosen={x['id']:x for x in core}
 for card in ranked[:8]:chosen.setdefault(card['id'],card)
 return list(chosen.values()),bounds,pool
