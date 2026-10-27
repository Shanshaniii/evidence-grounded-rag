"""Single JSON Schema source -> prompt, docs, validator and syntax-only aliases.

The bundle did not include its former docs/schema.json. The accompanying
schema reconstructs the exact fields/enums/limits visible in all supplied
prepared system contracts. Unknown historical x-aliases are not invented;
the normalizer still honors any explicit aliases in a locally verified schema.
"""
import pathlib,json,copy
ROOT=pathlib.Path(__file__).resolve().parents[1]
SCHEMA=json.loads((ROOT/'docs/schema.json').read_text(encoding='utf-8'))
def schema_origin():
 return copy.deepcopy(SCHEMA.get('x-schema-origin',{'status':'supplied_schema_without_origin_annotation'}))

def resolve(s):return SCHEMA['$defs'][s['$ref'].split('/')[-1]] if '$ref' in s else s
def normalize(raw):
 canonical=copy.deepcopy(raw);records=[];conflicts=[]
 def walk(v,s,path='$'):
  s=resolve(s)
  if isinstance(v,dict) and (s.get('type')=='object' or isinstance(s.get('type'),list) and 'object' in s['type']):
   for key,child in s['properties'].items():
    for alias in child.get('x-aliases',[]):
     if alias not in v:continue
     if key in v and v[key]!=v[alias]:conflicts.append(path+'.'+key);continue
     value=v.pop(alias);v.setdefault(key,value);records.append({'path':path,'from_key':alias,'to_key':key,'values_unchanged':True})
    if key in v:walk(v[key],child,path+'.'+key)
  elif isinstance(v,list) and s.get('type')=='array':
   for i,x in enumerate(v):walk(x,s['items'],path+'['+str(i)+']')
 walk(canonical,SCHEMA);return canonical,records,conflicts
def validate_structure(v):
 errors=[]
 def walk(v,s,path='$'):
  s=resolve(s);t=s['type'];types=t if isinstance(t,list) else [t]
  ok=any((x=='object' and isinstance(v,dict)) or (x=='array' and isinstance(v,list)) or (x=='string' and isinstance(v,str)) or (x=='boolean' and type(v) is bool) or (x=='null' and v is None) for x in types)
  if not ok:errors.append({'path':path,'code':'type','expected':t});return
  if v is None:return
  if 'enum' in s and v not in s['enum']:errors.append({'path':path,'code':'enum','allowed':s['enum']})
  if isinstance(v,str):
   if len(v)>s.get('maxLength',10**9) or len(v)<s.get('minLength',0):errors.append({'path':path,'code':'length'})
  elif isinstance(v,list):
   if len(v)>s.get('maxItems',10**9):errors.append({'path':path,'code':'maxItems'})
   for i,x in enumerate(v):walk(x,s['items'],path+'['+str(i)+']')
  elif isinstance(v,dict):
   for key in s.get('required',[]):
    if key not in v:errors.append({'path':path+'.'+key,'code':'missing'})
   for key in v:
    if key not in s['properties']:errors.append({'path':path+'.'+key,'code':'unknown_key'})
    else:walk(v[key],s['properties'][key],path+'.'+key)
 walk(v,SCHEMA);return errors
def field_table():
 rows=[]
 def walk(s,path):
  s=resolve(s);t=s['type'];row={'path':path,'type':t,'required_keys':s.get('required',[])}
  for key in ['enum','minLength','maxLength','maxItems','x-aliases']:
   if key in s:row[key]=s[key]
  rows.append(row)
  if t=='object' or isinstance(t,list) and 'object' in t:
   for k,v in s['properties'].items():walk(v,path+'.'+k)
  elif t=='array':walk(s['items'],path+'[]')
 walk(SCHEMA,'$');return rows
def prompt_contract():
 # Compact group spelling, generated directly from the same schema (no hand-copied enums/limits).
 def show(s):
  raw=s;s=resolve(s)
  if 'enum' in s:return '['+','.join(s['enum'])+']'
  if s['type']=='object' or isinstance(s['type'],list) and 'object' in s['type']:return ('null|' if isinstance(s['type'],list) else '')+'{'+','.join(k+':'+show(v) for k,v in s['properties'].items())+'}'
  if s['type']=='array':return '['+show(s['items'])+']<='+str(s['maxItems'])
  return ('str' if s['type']=='string' else 'null|str' if s['type']==['null','string'] else 'bool')+(f"({s.get('minLength',0)}..{s['maxLength']})" if 'maxLength' in s else '')
 return '仅JSON，键全必需，无额外键；字符限按Unicode码点。'+show(SCHEMA)
