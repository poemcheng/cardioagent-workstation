import io
import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
from PIL import Image
from cardio.app import create_app
from cardio.hospital import REPORT_FIELDS

PASSWORD='LocalResearch-test-7391'


class HospitalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.app=create_app(cls.temp.name,synchronous=True)
        for name,role in [('admin1','admin'),('reader1','radiologist'),('operator1','coordinator'),('viewer1','viewer')]:
            cls.app.auth.add(name,PASSWORD,role)

    @classmethod
    def tearDownClass(cls):
        cls.app.pool.shutdown(wait=True);cls.temp.cleanup()

    def client(self,name='reader1'):
        c=self.app.test_client();token=c.get('/api/session').json['csrf']
        response=c.post('/api/auth/login',json={'username':name,'password':PASSWORD},headers={'X-CSRF-Token':token})
        self.assertEqual(response.status_code,200,response.json)
        token=c.get('/api/session').json['csrf']
        return c,{'X-CSRF-Token':token}

    def new_study(self,c,h):
        raw=io.BytesIO();Image.fromarray(np.arange(4096,dtype=np.uint8).reshape(64,64)).save(raw,format='PNG')
        response=c.post('/api/cases',data={'modality':'MRI','images':(io.BytesIO(raw.getvalue()),'research.png')},headers=h)
        self.assertEqual(response.status_code,201)
        case_id=response.json['id'];suffix=case_id[-8:]
        body={'case_id':case_id,'patient_id':'P_'+suffix,'patient_name':'研究帳號資料','accession':'A-'+suffix,'description':'研究 MRI','department':'影像研究室','referrer':'local','priority':'routine'}
        response=c.post('/api/hospital/studies',json=body,headers=h)
        self.assertEqual(response.status_code,201,response.json)
        return response.json

    def fields(self):
        return {k:('人工研究所見：影像資料完整，判讀待研究覆核。' if k=='findings' else '研究報告，不作為臨床診斷。' if k=='impression' else '') for k in REPORT_FIELDS}

    def report(self,c,h,s,status='preliminary'):
        return c.post(f"/api/hospital/studies/{s['id']}/report",json={'version':0,'status':status,'fields':self.fields()},headers=h)

    def test_authentication_covers_images_and_legacy_api(self):
        c=self.app.test_client()
        for route in ['/api/cases','/api/cases/research-mri-01/slices/1','/reference/mri_result_comparison.png','/api/hospital/studies']:
            self.assertEqual(c.get(route).status_code,401,route)
        self.assertEqual(c.get('/research').status_code,302)

    def test_session_token_rotates_after_login(self):
        c=self.app.test_client();old=c.get('/api/session').json['csrf']
        c.post('/api/auth/login',json={'username':'reader1','password':PASSWORD},headers={'X-CSRF-Token':old})
        new=c.get('/api/session').json['csrf'];self.assertNotEqual(old,new)
        self.assertEqual(c.post('/api/auth/logout',json={},headers={'X-CSRF-Token':old}).status_code,403)
        self.assertEqual(c.post('/api/auth/logout',json={},headers={'X-CSRF-Token':new}).status_code,200)

    def test_setup_cannot_reopen_after_first_user(self):
        c=self.app.test_client();token=c.get('/api/session').json['csrf']
        r=c.post('/api/auth/setup',json={'username':'intruder','password':PASSWORD},headers={'X-CSRF-Token':token})
        self.assertEqual(r.status_code,403)

    def test_password_is_not_stored_in_cleartext(self):
        with self.app.store.connect() as db:row=db.execute("SELECT password_hash FROM users WHERE username='reader1'").fetchone()
        self.assertNotEqual(row[0],PASSWORD);self.assertNotIn(PASSWORD,row[0]);self.assertTrue(row[0].startswith('scrypt:'))

    def test_local_study_registration_and_patient_conflict(self):
        c,h=self.client('operator1');s=self.new_study(c,h)
        self.assertFalse(s['external_connected']);self.assertTrue(s['administrative_demo'])
        self.assertEqual(s['patient_id'],s['patient']['id']);self.assertEqual(s['workflow_status'],'available')
        r=c.post('/api/hospital/studies',json={'case_id':s['case_id'],'patient_id':s['patient_id'],'patient_name':'Changed','accession':s['accession']},headers=h)
        self.assertEqual(r.status_code,409)

    def test_viewer_cannot_mutate_but_can_logout(self):
        c,h=self.client('viewer1');s=c.get('/api/hospital/studies').json[0]
        self.assertEqual(c.post('/api/runs',json={},headers=h).status_code,403)
        self.assertEqual(c.post('/api/hospital/studies',json={},headers=h).status_code,403)
        self.assertEqual(c.post('/api/auth/logout',json={},headers=h).status_code,200)

    def test_operator_cannot_edit_or_sign_report(self):
        c,h=self.client('operator1');s=self.new_study(c,h)
        self.assertEqual(self.report(c,h,s).status_code,403)
        self.assertEqual(c.post(f"/api/hospital/studies/{s['id']}/report/sign",json={},headers=h).status_code,403)

    def test_preliminary_requires_findings_and_impression(self):
        c,h=self.client();s=self.new_study(c,h)
        r=c.post(f"/api/hospital/studies/{s['id']}/report",json={'version':0,'status':'preliminary','fields':{k:'' for k in REPORT_FIELDS}},headers=h)
        self.assertEqual(r.status_code,400)
        self.assertIsNone(c.get('/api/hospital/studies/'+s['id']).json['report'])

    def test_signed_report_is_immutable_and_actor_is_authenticated(self):
        c,h=self.client();s=self.new_study(c,h);r=self.report(c,h,s).json
        response=c.post(f"/api/hospital/studies/{s['id']}/report/sign",json={'version':r['version'],'review_confirmed':True,'username':'forged'},headers=h)
        self.assertEqual(response.status_code,200,response.json)
        signed=response.json;self.assertEqual(signed['signature']['username'],'reader1')
        r=c.post(f"/api/hospital/studies/{s['id']}/report",json={'version':signed['version'],'status':'draft','fields':self.fields()},headers=h)
        self.assertEqual(r.status_code,409)
        self.assertEqual(c.get('/api/hospital/studies/'+s['id']).json['report']['fields'],signed['fields'])

    def test_signing_requires_preliminary_and_explicit_confirmation(self):
        c,h=self.client();s=self.new_study(c,h);r=self.report(c,h,s,'draft').json
        path=f"/api/hospital/studies/{s['id']}/report/sign"
        self.assertEqual(c.post(path,json={'version':r['version'],'review_confirmed':True},headers=h).status_code,409)
        r=c.post(f"/api/hospital/studies/{s['id']}/report",json={'version':r['version'],'status':'preliminary','fields':self.fields()},headers=h).json
        self.assertEqual(c.post(path,json={'version':r['version']},headers=h).status_code,400)
        self.assertEqual(c.post(path,json={'version':True,'review_confirmed':True},headers=h).status_code,409)

    def test_optimistic_report_version_prevents_lost_updates(self):
        c,h=self.client();s=self.new_study(c,h);r=self.report(c,h,s,'draft')
        self.assertEqual(r.status_code,200);self.assertEqual(self.report(c,h,s,'draft').status_code,409)
        self.assertEqual(c.get('/api/hospital/studies/'+s['id']).json['report']['version'],1)

    def test_addendum_retains_original_and_complete_history(self):
        c,h=self.client();s=self.new_study(c,h);r=self.report(c,h,s).json
        r=c.post(f"/api/hospital/studies/{s['id']}/report/sign",json={'version':r['version'],'review_confirmed':True},headers=h).json
        original=r['fields'].copy()
        r=c.post(f"/api/hospital/studies/{s['id']}/report/addendum",json={'version':r['version'],'reason':'研究資料補充','text':'補充說明，保留原始報告。'},headers=h)
        self.assertEqual(r.status_code,200,r.json);self.assertEqual(r.json['fields'],original)
        history=c.get(f"/api/hospital/studies/{s['id']}/history").json
        self.assertEqual([r['status'] for r in history['revisions']],['preliminary','final','amended'])
        self.assertEqual(history['revisions'][1]['addenda'],[]);self.assertEqual(len(history['revisions'][2]['addenda']),1)

    def test_fhir_status_mapping_and_bundle_references(self):
        c,h=self.client();s=self.new_study(c,h);self.report(c,h,s,'draft')
        b=c.get(f"/api/hospital/studies/{s['id']}/fhir").json
        self.assertEqual(b['type'],'collection');self.assertEqual(len(b['entry']),3)
        urls={e['fullUrl'] for e in b['entry']};dr=next(e['resource'] for e in b['entry'] if e['resource']['resourceType']=='DiagnosticReport')
        self.assertEqual(dr['status'],'partial');self.assertIn(dr['subject']['reference'],urls);self.assertIn(dr['basedOn'][0]['reference'],urls)
        self.assertNotIn('ImagingStudy',[e['resource']['resourceType'] for e in b['entry']])

    def test_patient_and_report_do_not_cross_studies(self):
        c,h=self.client();a=self.new_study(c,h);b=self.new_study(c,h);self.report(c,h,a)
        b=c.get('/api/hospital/studies/'+b['id']).json;self.assertIsNone(b['report']);self.assertNotEqual(a['patient_id'],b['patient_id'])

    def test_user_management_requires_admin(self):
        c,h=self.client();self.assertEqual(c.get('/api/auth/users').status_code,403)
        self.assertEqual(c.post('/api/auth/users',json={},headers=h).status_code,403)
        c,h=self.client('admin1');r=c.get('/api/auth/users');self.assertEqual(r.status_code,200)
        self.assertNotIn('password',json.dumps(r.json))

    def test_failed_login_is_rate_limited(self):
        c=self.app.test_client();token=c.get('/api/session').json['csrf']
        for i in range(6):response=c.post('/api/auth/login',json={'username':'nonexistent-rate-test','password':'wrong'},headers={'X-CSRF-Token':token})
        self.assertEqual(response.status_code,403);self.assertIn('次數',response.json['error'])

    def test_audit_covers_report_read_export_and_signature(self):
        c,h=self.client();s=self.new_study(c,h);r=self.report(c,h,s).json
        c.get('/api/hospital/studies/'+s['id']);c.get(f"/api/hospital/studies/{s['id']}/fhir")
        c.post(f"/api/hospital/studies/{s['id']}/report/sign",json={'version':r['version'],'review_confirmed':True},headers=h)
        kinds={e['kind'] for e in self.app.store.events(s['id'])}
        self.assertTrue({'study_registered','study_opened','report_saved','report_signed','fhir_exported'}<=kinds)
        self.assertTrue(self.app.store.verify()['valid'])


if __name__=='__main__':unittest.main()
