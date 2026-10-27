from reviewed.unicode_utils import unicode_scalar_error

def citation_schema_error(c):
 if not isinstance(c,dict):return 'citation_not_object'
 if unicode_scalar_error(c):return 'citation_non_scalar_unicode'
 if not isinstance(c.get('article_id'),str) or not c['article_id']:return 'citation_article_id_invalid'
 if not isinstance(c.get('quote',c.get('text')),str) or not c.get('quote',c.get('text')):return 'citation_quote_invalid'
 if c.get('kind')=='body':
  if type(c.get('start')) is not int or type(c.get('end')) is not int or not 0<=c['start']<c['end']:return 'citation_span_invalid'
 elif c.get('kind')=='metadata':
  if c.get('field')!='title':return 'citation_metadata_field_invalid'
 else:return 'citation_kind_invalid'
 return None

def answer_schema_error(answer):
 if not isinstance(answer,dict):return 'answer_not_object'
 if unicode_scalar_error(answer):return 'answer_non_scalar_unicode'
 if answer.get('answer_status') not in ['supported','insufficient_evidence','conflict'] or not isinstance(answer.get('answer'),str):return 'answer_status_or_text_invalid'
 if not isinstance(answer.get('missing_evidence'),list) or any(not isinstance(x,str) for x in answer['missing_evidence']):return 'missing_evidence_not_string_array'
 if not isinstance(answer.get('claims'),list):return 'claims_not_array'
 if answer['answer_status']=='supported' and not answer['claims']:return 'supported_answer_missing_claims'
 seen=set()
 for claim in answer['claims']:
  if not isinstance(claim,dict):return 'claim_not_object'
  if not all(isinstance(claim.get(k),str) and claim[k] for k in ['claim_id','text','subject','event_time','state']):return 'claim_fields_invalid'
  if claim['claim_id'] in seen:return 'duplicate_claim_id'
  seen.add(claim['claim_id'])
  if claim['state'] not in ['planned','conditional','completed_reported','historical','conflict','unknown']:return 'claim_state_invalid'
  citations=claim.get('citations')
  if not isinstance(citations,list) or not citations:return 'citations_not_nonempty_array'
  for citation in citations:
   error=citation_schema_error(citation)
   if error:return error
 return None
