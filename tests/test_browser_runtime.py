"""Exercise the public visitor's real in-process WSGI transport and pipeline."""
import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from cardio.browser_runtime import BrowserRuntime


class BrowserRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.runtime=BrowserRuntime(Path(self.temp.name)/'one')
        self.csrf=self.get('/api/session')['csrf']

    def tearDown(self):
        self.runtime.close();self.temp.cleanup()

    def raw(self,path,method='GET',body=None,**extra):
        payload={'path':path,'method':method,'headers':{'X-CSRF-Token':getattr(self,'csrf',''),'Content-Type':'application/json'},**extra}
        if body is not None:payload['body']=json.dumps(body)
        return json.loads(self.runtime.request(json.dumps(payload)))

    def get(self,path):
        result=self.raw(path);self.assertEqual(result['status'],200,result)
        return json.loads(base64.b64decode(result['base64']))

    def post(self,path,body):
        result=self.raw(path,'POST',body);self.assertIn(result['status'],(200,201,202),result)
        return json.loads(base64.b64decode(result['base64']))

    def test_guest_has_no_shared_account_or_network_service(self):
        status=self.get('/api/auth/status')
        self.assertEqual(status['user']['username'],'browser-visitor')
        self.assertEqual(status['user']['role'],'radiologist')
        self.assertFalse(status['setup_required']);self.assertFalse(status['shared_server'])
        for path in ('/api/auth/setup','/api/auth/login','/api/auth/users'):
            self.assertEqual(self.raw(path,'POST',{})['status'],403)
        with patch('urllib.request.urlopen',side_effect=AssertionError('no service requests')):
            model=self.get('/api/demo/status')
        self.assertFalse(model['available_models'])

    def test_real_mri_reference_pipeline_and_png_are_not_precomputed(self):
        result=self.post('/api/analyses',{'case_id':'research-mri-01','mode':'inspect','center':4,'segmentation_mode':'provided_reference'})
        self.assertEqual(result['reconstruction']['statistics']['foreground_voxels'],75846)
        self.assertEqual(result['reconstruction']['mesh']['triangles'],2412)
        self.assertIsNone(result['reconstruction']['statistics']['physical_volume_mm3'])
        self.assertIsNone(result['comparison']['segmentation']['dice'])
        self.assertFalse(result['recognition']['inference_executed'])
        png=self.raw('/api/analyses/'+result['id']+'/slices/4?view=mask')
        image=np.asarray(Image.open(io.BytesIO(base64.b64decode(png['base64']))))
        self.assertGreater(np.count_nonzero(image),1000)
        self.assertEqual(self.raw('/api/analyses/'+result['id']+'/mesh.obj')['status'],200)

    def test_visitors_have_independent_sqlite_analyses_and_csrf(self):
        result=self.post('/api/analyses',{'case_id':'demo-ct-positive','mode':'demo','center':1})
        other=BrowserRuntime(Path(self.temp.name)/'two')
        try:
            response=json.loads(other.request(json.dumps({'path':'/api/analyses/'+result['id']})))
            self.assertEqual(response['status'],404)
            token=json.loads(base64.b64decode(json.loads(other.request(json.dumps({'path':'/api/session'})))['base64']))['csrf']
            self.assertNotEqual(token,self.csrf)
        finally:other.close()

    def test_multipart_import_stays_inside_the_visitor_workspace(self):
        file=io.BytesIO();Image.new('L',(64,64),80).save(file,format='PNG')
        parts=[{'name':'images','filename':'local-test.png','type':'image/png','base64':base64.b64encode(file.getvalue()).decode()},
               {'name':'modality','value':'MRI'},{'name':'label','value':'Browser local fixture'}]
        response=self.raw('/api/cases','POST',multipart=parts)
        self.assertEqual(response['status'],201,response)
        result=json.loads(base64.b64decode(response['base64']))
        self.assertEqual(self.get('/api/cases/'+result['id'])['slice_count'],1)

    def test_report_signature_is_labelled_as_demo_attestation(self):
        self.assertIn('browser-local demo',self.runtime.app.hospital.signature_kind)
        result=self.post('/api/analyses',{'case_id':'demo-ct-positive','mode':'demo','center':1})
        review=self.post('/api/analyses/'+result['id']+'/review',{'version':result['version'],'decision':'approve','review_confirmed':True})
        self.assertEqual(review['review']['actor'],'browser-visitor')
        self.assertEqual(self.raw('/api/analyses/'+result['id']+'/report-draft')['status'],200)


if __name__=='__main__':unittest.main()
