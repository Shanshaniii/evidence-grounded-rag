import unittest,json,copy,pathlib,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import app
class ExportDemo(unittest.TestCase):
 def test_synthetic_complete_pipeline_no_model(self):
  case=json.loads((app.ROOT/'examples/synthetic_case.json').read_text('utf-8'));raw=json.loads((app.ROOT/'examples/synthetic_response.json').read_text('utf-8'));p,cards=app.request(case,'placeholder');before=copy.deepcopy(raw);r=app.diagnose(raw,case,cards);self.assertEqual(raw,before);self.assertEqual(r['schema_errors'],[]);self.assertEqual(r['risks'],[]);self.assertTrue(all(x['status']=='unique' for x in r['locations']));self.assertNotIn('pub',json.loads(p['prompt'])['sources']['S1'])
 def test_unsupported_year_not_program_corrected(self):
  case=json.loads((app.ROOT/'examples/synthetic_case.json').read_text('utf-8'));raw=json.loads((app.ROOT/'examples/synthetic_response.json').read_text('utf-8'));raw['claims'][0]['explicit_event_date'].update(value='2025-06-18',precision='full_date');before=copy.deepcopy(raw);_,cards=app.request(case,'placeholder');r=app.diagnose(raw,case,cards);self.assertIn('event_date_not_literal_in_quote',r['risks']);self.assertEqual(raw,before)
if __name__=='__main__':unittest.main()
