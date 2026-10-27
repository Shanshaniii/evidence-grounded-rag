"""Scalar validation and safe JSON diagnostics; do not normalize malformed answer facts."""
import json,math

def unicode_scalar_error(value,depth=0,seen=None):
 if depth>64:return 'unicode_structure_depth_limit'
 if isinstance(value,str):return 'non_scalar_unicode_string' if any(0xD800<=ord(c)<=0xDFFF for c in value) else None
 if not isinstance(value,(dict,list,tuple)):return None
 seen=set() if seen is None else seen
 if id(value) in seen:return 'cyclic_diagnostic_structure'
 seen.add(id(value))
 values=[x for pair in value.items() for x in pair] if isinstance(value,dict) else value
 for item in values:
  error=unicode_scalar_error(item,depth+1,seen)
  if error:seen.remove(id(value));return error
 seen.remove(id(value));return None

def escape_non_scalars(text):
 return ''.join('\\u%04x'%ord(c) if 0xD800<=ord(c)<=0xDFFF else c for c in text)

def safe_diagnostics(value,depth=0,seen=None):
 if depth>64:return '[DIAGNOSTIC_DEPTH_LIMIT]'
 if isinstance(value,str):return escape_non_scalars(value)
 if isinstance(value,float) and not math.isfinite(value):return '[NON_FINITE_DIAGNOSTIC_NUMBER]'
 if value is None or isinstance(value,(bool,int,float)):return value
 if isinstance(value,bytes):return {'bytes_hex':value.hex()}
 if not isinstance(value,(dict,list,tuple)):return '[UNSUPPORTED_DIAGNOSTIC_TYPE]'
 seen=set() if seen is None else seen
 if id(value) in seen:return '[DIAGNOSTIC_CYCLE]'
 seen.add(id(value))
 if isinstance(value,dict):out={escape_non_scalars(str(k)):safe_diagnostics(v,depth+1,seen) for k,v in value.items()}
 else:out=[safe_diagnostics(v,depth+1,seen) for v in value]
 seen.remove(id(value));return out

def safe_json(value,pretty=False):
 # ASCII escapes serialize valid non-BMP scalars as legal JSON pairs; illegal diagnostics are literal escaped text.
 return json.dumps(safe_diagnostics(value),ensure_ascii=True,indent=2 if pretty else None,allow_nan=False)
