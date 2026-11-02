"""Deployment and source-integrity regressions, not clinical model validation."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cardio.aipc import AIPCDemo,input_for_model,lexical_baseline,turns,verify_output
from cardio.app import create_app
from cardio.planner import Planner
from cardio.store import Store

ROOT=Path(__file__).resolve().parents[1]
MODEL={'id':'test-local','downloaded':True,'recipe':'flm'}


def setUpModule():
    from scripts.prepare_aipc_data import build
    build()


class AIPCDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases={c['id']:c for c in map(json.loads,(ROOT/'datasets/mts_dialog/cases.jsonl').read_text(encoding='utf-8').splitlines())}

    def test_pinned_data_splits_and_hash(self):
        manifest=json.loads((ROOT/'datasets/mts_dialog/manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(len(self.cases),1713)
        self.assertEqual([sum(c['split']==s for c in self.cases.values()) for s in ('train','validation','test_1','test_2','synthetic_demo')],[1201,100,200,200,12])
        self.assertEqual(len(manifest['normalized_dialogue_duplicates']),3)
        self.assertFalse(any(d['cross_split'] for d in manifest['normalized_dialogue_duplicates']))
        self.assertEqual(hashlib.sha256((ROOT/'datasets/mts_dialog/cases.jsonl').read_bytes()).hexdigest(),manifest['cases_sha256'])

    def test_gold_and_intake_answers_are_not_model_inputs(self):
        for case in self.cases.values():
            payload=input_for_model(case)
            self.assertEqual(set(payload),{'task','allowed_sections','turns'})
            self.assertTrue(all(set(t)=={'id','text'} for t in payload['turns']))
            self.assertIsNone(case['paired_imaging_case_id'])

    def test_negation_and_numeric_changes_are_blocked(self):
        case={'dialogue':'Doctor: Any chest pain?\nPatient: No chest pain; aspirin 81 mg.'}
        output={'predicted_section':'medications','evidence':[{'turn_id':'t2','section':'medications','quote':'Patient: No chest pain; aspirin 81 mg.'}]}
        section,checked=verify_output(case,output)
        self.assertEqual(checked[0]['preceding_turn']['id'],'t1')
        self.assertEqual(case['dialogue'][checked[0]['start']:checked[0]['end']],output['evidence'][0]['quote'])
        for text in ('Patient: chest pain; aspirin 81 mg.','Patient: No chest pain; aspirin 810 mg.','No chest pain; aspirin 81 mg.'):
            changed=copy.deepcopy(output);changed['evidence'][0]['quote']=text
            with self.assertRaises(ValueError):verify_output(case,changed)

    def test_invalid_and_repeated_evidence_cannot_pass(self):
        case=self.cases['synthetic-01'];output=lexical_baseline(case)
        for key,value in [('turn_id','t9999'),('section','invented')]:
            changed=copy.deepcopy(output);changed['evidence'][0][key]=value
            with self.assertRaises(ValueError):verify_output(case,changed)
        output['evidence'].append(output['evidence'][0])
        with self.assertRaises(ValueError):verify_output(case,output)


class AIPCServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        self.planner=Planner({});self.service=AIPCDemo(ROOT,self.path,Store(self.path/'data.sqlite3'),self.planner)

    def tearDown(self):self.temp.cleanup()

    def test_all_synthetic_missing_fields_are_exact(self):
        for case in self.service.cases().values():
            if case['split']!='synthetic_demo':continue
            result=self.service.generate(case['id'],'baseline',None,'test-actor')
            self.assertEqual(sorted(result['intake_check']['missing_fields']),sorted(case['gold']['expected_missing']))
            self.assertFalse(result['automatic_orders_created'])
            self.assertFalse(result['automatically_saved_to_medical_report'])

    def test_acute_bypasses_busy_llm_and_unavailable_service(self):
        self.service.gate.acquire()
        try:
            with patch.object(self.planner,'request',side_effect=AssertionError('Must not call model')):
                result=self.service.generate('synthetic-07','llm',None,'test-actor')
            self.assertEqual(result['status'],'urgent_human_workflow')
            self.assertEqual(result['engine']['source'],'explicit_acute_flag_rules')
        finally:self.service.gate.release()

    def test_local_protocol_receipt_does_not_claim_npu(self):
        case=self.service.case('synthetic-01');reply={'model':MODEL['id'],'choices':[{'message':{'content':json.dumps(lexical_baseline(case))}}]}
        with patch.object(self.planner,'models',return_value={'data':[MODEL]}),patch.object(self.planner,'request',return_value=reply) as request:
            result=self.service.generate(case['id'],'llm',MODEL['id'],'test-actor')
        self.assertEqual(result['engine']['source'],'lemonade_local_model')
        self.assertFalse(result['engine']['device_execution_verified'])
        self.assertNotIn('gold',request.call_args.args[1]['messages'][1]['content'])

    def test_cloud_route_and_model_failure_do_not_fall_back(self):
        for model in (MODEL|{'cloud_provider':'external'},MODEL|{'downloaded':False}):
            with patch.object(self.planner,'models',return_value={'data':[model]}):
                with self.assertRaises(ValueError):self.service.generate('synthetic-01','llm',MODEL['id'],'test-actor')
        with patch.object(self.planner,'models',return_value={'data':[MODEL]}),patch.object(self.planner,'request',side_effect=TimeoutError('test timeout')):
            with self.assertRaises(TimeoutError):self.service.generate('synthetic-01','llm',MODEL['id'],'test-actor')
        self.assertEqual(list((self.path/'aipc').glob('*.json')),[])

    def test_model_mismatch_and_unverified_quote_are_blocked(self):
        case=self.service.case('synthetic-01')
        raw=lexical_baseline(case)
        for model,body in [('different-model',raw),(MODEL['id'],dict(raw,evidence=[{'turn_id':'t1','section':'genhx','quote':'fabricated'}]))]:
            reply={'model':model,'choices':[{'message':{'content':json.dumps(body)}}]}
            with patch.object(self.planner,'models',return_value={'data':[MODEL]}),patch.object(self.planner,'request',return_value=reply):
                with self.assertRaises(ValueError):self.service.generate(case['id'],'llm',MODEL['id'],'test-actor')
        self.assertEqual(list((self.path/'aipc').glob('*.json')),[])

    def test_saved_export_tampering_is_detected(self):
        result=self.service.generate('synthetic-01','baseline',None,'test-actor')
        self.assertEqual(self.service.get(result['id']),result)
        path=self.path/'aipc'/(result['id']+'.json');path.write_text('{}',encoding='utf-8')
        with self.assertRaises(ValueError):self.service.get(result['id'])


class AIPCRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.app=create_app(self.temp.name,synchronous=True);self.client=self.app.test_client()
        token=self.client.get('/api/session').json['csrf']
        response=self.client.post('/api/auth/setup',json={'username':'aipc-test-admin','password':'Synthetic-test-password-7391'},headers={'X-CSRF-Token':token})
        self.assertEqual(response.status_code,200)
        self.headers={'X-CSRF-Token':self.client.get('/api/session').json['csrf']}

    def tearDown(self):self.app.pool.shutdown(wait=True);self.temp.cleanup()

    def test_api_requires_auth_and_csrf(self):
        guest=self.app.test_client()
        self.assertEqual(guest.get('/aipc').location,'/?next=aipc')
        self.assertEqual(guest.get('/api/aipc/cases').status_code,401)
        self.assertEqual(self.client.post('/api/aipc/summaries',json={'case_id':'synthetic-01'}).status_code,403)

    def test_complete_authenticated_baseline_and_export(self):
        self.assertEqual(self.client.get('/aipc').status_code,200)
        self.assertEqual(len(self.client.get('/api/aipc/cases').json),1713)
        self.assertNotIn('gold',self.client.get('/api/aipc/cases/synthetic-01').json)
        response=self.client.post('/api/aipc/summaries',json={'case_id':'synthetic-01','mode':'baseline'},headers=self.headers)
        self.assertEqual(response.status_code,201,response.json)
        result=response.json
        self.assertEqual(result['actor'],'aipc-test-admin')
        exported=self.client.get('/api/aipc/summaries/'+result['id']+'/export')
        self.assertEqual(exported.json,result)
        self.assertIn('attachment',exported.headers['Content-Disposition'])

    def test_client_cannot_spoof_actor_or_add_clinical_decision(self):
        response=self.client.post('/api/aipc/summaries',json={'case_id':'synthetic-01','actor':'someone-else','procedure':'CABG'},headers=self.headers)
        self.assertEqual(response.status_code,400)
