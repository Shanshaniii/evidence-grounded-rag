"""Remove unexpected reasoning before persistence and API publication; no inference or authorization."""
import json,re
from reviewed.codec import strict_loads
from reviewed.unicode_utils import safe_json
REASONING_KEYS={'thinking','thinkingcontent','thinkingtext','reasoning','reasoningcontent','reasoningtext','reasoningbody','reasoningdetails','chainofthought','analysis'}
OPEN=re.compile(r'<\s*(think|thinking|reasoning|analysis)(?:\s[^>]*|)\s*>',re.I)
CLOSE=re.compile(r'<\s*/\s*(?:think|thinking|reasoning|analysis)\s*>',re.I)
def is_reasoning_key(key): return re.sub(r'[^a-z]','',str(key).lower()) in REASONING_KEYS
def strip_reasoning(value,depth=0):
 if depth>48: return '[DIAGNOSTIC_DEPTH_LIMIT]'
 if isinstance(value,dict): return {k:strip_reasoning(v,depth+1) for k,v in value.items() if not is_reasoning_key(k)}
 if isinstance(value,(list,tuple)): return [strip_reasoning(v,depth+1) for v in value]
 if not isinstance(value,str): return value
 # Native raw_response and response can contain nested JSON strings; filter inside them too.
 try: nested=strict_loads(value)
 except (ValueError,TypeError,RecursionError): nested=None
 if isinstance(nested,(dict,list)):
  clean=strip_reasoning(nested,depth+1)
  return safe_json(clean) if clean!=nested else value
 if isinstance(nested,str) and nested!=value:
  clean=strip_reasoning(nested,depth+1)
  return safe_json(clean) if clean!=nested else value
 text=value
 # If only a closing tag survived a partial response, everything before it may be reasoning.
 while True:
  opening=OPEN.search(text);closing=CLOSE.search(text)
  if closing and (not opening or closing.start()<opening.start()): text='[REASONING_REMOVED]'+text[closing.end():];continue
  if not opening: break
  closing=CLOSE.search(text,opening.end())
  text=text[:opening.start()]+'[REASONING_REMOVED]'+(text[closing.end():] if closing else '')
 # Partial/malformed JSON diagnostics must not preserve a thinking field's value.
 if re.search(r'["\'](?:thinking|reasoning(?:_content)?|analysis|chain_of_thought)["\']\s*:',text,re.I): return '[MALFORMED_REASONING_DIAGNOSTIC_REMOVED]'
 return text
def safe_response_representation(rep):
 if rep.get('representation')=='sanitized_diagnostic_text':
  # Invalid/partial wire cannot safely distinguish plain reasoning from a partial answer.
  return {k:v for k,v in rep.items() if k!='text'}|{'text':'[INVALID_OR_PARTIAL_WIRE_BODY_OMITTED]'}
 return strip_reasoning(rep)
