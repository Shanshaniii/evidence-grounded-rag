"""Decoded/nested known-secret sanitization; raw logs are safe representations, never reversible original bytes."""
import json,re
from reviewed.unicode_utils import safe_diagnostics,safe_json
from reviewed.codec import strict_loads
PATTERN=re.compile(r'\\u[0-9a-fA-F]{4}|\\x[0-9a-fA-F]{2}|\\[\\/"bfnrt]')
def unescape_once(text):
 def sub(m):
  x=m.group()
  try:return chr(int(x[2:],16)) if x.startswith('\\x') else json.loads('"'+x+'"')
  except (ValueError,TypeError):return x
 return PATTERN.sub(sub,text)
def scrub_string(value,token,depth=0):
 if not token:return value
 direct=value.replace(token,'[REDACTED]')
 if depth>=32:return direct if direct!=value else '[REDACTED_DEPTH_LIMIT_DIAGNOSTIC]'
 # Native response embeds another JSON document; decode recursively before logging it.
 try:nested=strict_loads(value)
 except (ValueError,TypeError,RecursionError):nested=None
 if isinstance(nested,(dict,list)):
  sanitized=scrub(nested,token,depth+1)
  return safe_json(sanitized) if sanitized!=nested else direct
 if isinstance(nested,str) and nested!=value:
  sanitized=scrub_string(nested,token,depth+1)
  if sanitized!=nested:return safe_json(sanitized)
 decoded=value
 for _ in range(32):
  next_value=unescape_once(decoded)
  if next_value==decoded:break
  decoded=next_value
  if token in decoded:return safe_diagnostics(decoded.replace(token,'[REDACTED]'))
 return direct

def scrub(value,token,depth=0):
 if isinstance(value,str):return safe_diagnostics(scrub_string(value,token,depth))
 if isinstance(value,dict):return {scrub_string(str(k),token,depth+1):scrub(v,token,depth+1) for k,v in value.items()}
 if isinstance(value,(list,tuple)):return [scrub(v,token,depth+1) for v in value]
 return safe_diagnostics(value)

def safe_raw(raw,token):
 # Strict UTF8 validation happens separately; alternate decoding here is diagnostic ONLY.
 try:
  text=raw.decode('utf-8','strict');encoding='utf8'
 except UnicodeError:
  encoding='invalid_utf8_diagnostic'
  if raw.startswith((b'\xff\xfe',b'\xfe\xff')):
   try:text=raw.decode('utf-16','strict');encoding='utf16_diagnostic_only'
   except UnicodeError:text=raw.decode('utf-8','backslashreplace')
  else:text=raw.decode('utf-8','backslashreplace')
 try:
  obj=strict_loads(text);sanitized=scrub(obj,token);representation={'representation':'sanitized_decoded_json','source_encoding':encoding,'value':sanitized}
 except (ValueError,TypeError,RecursionError):representation={'representation':'sanitized_diagnostic_text','source_encoding':encoding,'text':scrub_string(text,token)}
 return safe_json(representation,pretty=True).encode('utf-8'),representation
