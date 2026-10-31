import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
import numpy as np
from PIL import Image

from cardio.app import create_app
from cardio.imaging import Detector, nms, read_upload, sha256
from cardio.planner import Planner, TOOLS

COMPLETE={"age":62,"acute":False,"history_complete":True,"consent":True}


class WorkstationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.app=create_app(self.temp.name,synchronous=True,access_control=False)
        self.client=self.app.test_client();self.headers={"X-CSRF-Token":self.client.get('/api/session').json['csrf']}

    def tearDown(self):
        self.app.pool.shutdown(wait=True);self.temp.cleanup()

    def start(self, case="demo-ct-positive",context=None,**kwargs):
        body={"case_id":case,"mode":"demo","planner":"rules","center":1,"context":COMPLETE if context is None else context}|kwargs
        r=self.client.post('/api/runs',json=body,headers=self.headers)
        self.assertEqual(r.status_code,202,r.json)
        return self.client.get('/api/runs/'+r.json['id']).json

    def review(self,r,decision="approve",**kwargs):
        return self.client.post(f"/api/runs/{r['id']}/review",json={"decision":decision,"reviewer":"test-reviewer","reason":"","version":r['version']}|kwargs,headers=self.headers)

    def test_positive_requires_review_and_is_idempotent(self):
        r=self.start();self.assertEqual(r['status'],'pending_review');self.assertEqual(r['completed'],TOOLS)
        self.assertEqual(len(r['vision']['candidates']),2);self.assertFalse(r['vision']['trained']);self.assertEqual(r['tasks'],[])
        self.assertEqual(self.review(r).status_code,200)
        r=self.client.get('/api/runs/'+r['id']).json;self.assertEqual(len(r['tasks']),2)
        self.assertTrue(all(t['demo'] for t in r['tasks']));self.assertEqual(self.review(r).status_code,400)
        self.assertEqual(len(self.app.store.tasks(r['id'])),2)

    def test_zero_candidates_remain_explicit(self):
        r=self.start('demo-ct-zero');self.assertEqual(r['vision']['candidates'],[])
        self.assertEqual(r['draft']['candidate_count'],0);self.assertIn('零候選',r['draft']['text'])

    def test_missing_context_resume_preserves_vision_settings(self):
        r=self.start(context=COMPLETE|{'age':None,'history_complete':False})
        self.assertEqual(r['status'],'pending_data');self.assertNotIn('draft',r)
        before=r['vision'];self.assertEqual(len(r['completed']),2)
        response=self.client.post(f"/api/runs/{r['id']}/resume",json={'context':COMPLETE,'version':r['version']},headers=self.headers)
        self.assertEqual(response.status_code,202)
        after=self.client.get('/api/runs/'+r['id']).json
        self.assertEqual(after['vision'],before);self.assertEqual(after['settings'],r['settings'])
        self.assertEqual(after['draft']['context']['age'],62);self.assertEqual(after['status'],'pending_review')
        self.assertEqual(sum(t['tool']=='detect_candidates' for t in after['tool_log']),1)

    def test_stale_resume_is_rejected(self):
        r=self.start(context=COMPLETE|{'age':None})
        response=self.client.post(f"/api/runs/{r['id']}/resume",json={'context':COMPLETE,'version':99},headers=self.headers)
        self.assertEqual(response.status_code,400)

    def test_acute_flag_never_calls_planner_or_detector(self):
        self.app.agent.planner.choose=Mock(side_effect=AssertionError('should not call'))
        r=self.start(context=COMPLETE|{'acute':True},planner='lemonade')
        self.assertEqual(r['status'],'emergency');self.assertEqual(r['completed'],[]);self.assertEqual(r['tasks'],[])
        self.app.agent.planner.choose.assert_not_called()

    def test_unknown_acute_requires_confirmation_before_tools(self):
        r=self.start(context=COMPLETE|{'acute':None});self.assertEqual(r['status'],'pending_data')
        self.assertEqual(r['missing'],['acute']);self.assertEqual(r['tool_log'],[])

    def test_mri_is_blocked_from_cac_inference(self):
        r=self.start('research-mri-01',mode='model');self.assertEqual(r['status'],'blocked')
        self.assertIn('MRI',r['error']);self.assertNotIn('vision',r)

    def test_no_synthetic_inference_on_real_image(self):
        r=self.client.post('/api/runs',json={'case_id':'research-mri-01','mode':'demo','center':1,'context':COMPLETE},headers=self.headers)
        self.assertEqual(r.status_code,400)

    def test_missing_trained_weight_fails_closed(self):
        self.app.detector.manifest=Mock(side_effect=ValueError('model missing'))
        r=self.start(mode='model');self.assertEqual(r['status'],'blocked');self.assertNotIn('vision',r)
        self.assertEqual(r['tasks'],[])

    def test_rejection_is_terminal(self):
        r=self.start();self.assertEqual(self.review(r,'reject').status_code,400)
        self.assertEqual(self.review(r,'reject',reason='影像來源待確認').status_code,200)
        r=self.client.get('/api/runs/'+r['id']).json
        self.assertEqual(r['status'],'rejected');self.assertEqual(r['tasks'],[])
        self.assertEqual(self.review(r).status_code,400)

    def test_early_approval_cannot_create_tasks(self):
        r=self.start(context=COMPLETE|{'consent':False})
        self.assertEqual(r['status'],'pending_data');self.assertEqual(self.review(r).status_code,400)
        self.assertEqual(self.app.store.tasks(r['id']),[])

    def test_csrf_and_host_boundaries(self):
        self.assertEqual(self.client.post('/api/runs',json={}).status_code,403)
        self.assertEqual(self.client.get('/api/cases',headers={'Host':'attacker.example'}).status_code,400)
        self.assertEqual(self.client.get('/api/cases',environ_base={'REMOTE_ADDR':'192.168.1.2'}).status_code,403)

    def test_reference_annotations_are_case_scoped(self):
        cases=self.client.get('/api/cases').json
        data={c['id']:c for c in cases};self.assertEqual(data['research-mri-01']['reference_boxes'],{})
        self.assertTrue(data['demo-ct-positive']['reference_boxes']);self.assertFalse(data['demo-ct-zero']['reference_boxes'])

    def test_upload_and_modality_declaration(self):
        out=io.BytesIO();Image.fromarray(np.arange(4096,dtype=np.uint8).reshape(64,64)).save(out,format='PNG')
        r=self.client.post('/api/cases',data={'modality':'MRI','images':(io.BytesIO(out.getvalue()),'private_patient_name.png')},headers=self.headers)
        self.assertEqual(r.status_code,201,r.json);case=self.app.store.case(r.json['id'])
        self.assertFalse(case['is_hu']);self.assertNotIn('private_patient_name',json.dumps(case))
        self.assertEqual(self.client.get(f"/api/cases/{case['id']}/slices/0").status_code,200)

    def test_multislice_upload_requires_order_confirmation(self):
        out=io.BytesIO();Image.fromarray(np.ones((64,64),dtype=np.uint8)).save(out,format='PNG')
        r=self.client.post('/api/cases',data={'modality':'CT','images':[(io.BytesIO(out.getvalue()),'a.png'),(io.BytesIO(out.getvalue()),'b.png')]},headers=self.headers)
        self.assertEqual(r.status_code,400);self.assertIn('順序',r.json['error'])

    def test_dicom_modality_mismatch_and_hu_preservation(self):
        from pydicom.dataset import FileDataset, FileMetaDataset
        from pydicom.uid import ExplicitVRLittleEndian, generate_uid
        fm=FileMetaDataset();fm.TransferSyntaxUID=ExplicitVRLittleEndian;fm.MediaStorageSOPClassUID=generate_uid();fm.MediaStorageSOPInstanceUID=generate_uid()
        ds=FileDataset(None,{},file_meta=fm,preamble=b'\0'*128);ds.Modality='CT';ds.Rows=32;ds.Columns=32;ds.SamplesPerPixel=1
        ds.PhotometricInterpretation='MONOCHROME2';ds.BitsAllocated=16;ds.BitsStored=16;ds.HighBit=15;ds.PixelRepresentation=0
        ds.RescaleSlope=1;ds.RescaleIntercept=-1024;ds.PatientName='private';ds.PixelData=np.arange(1024,dtype=np.uint16).tobytes()
        out=io.BytesIO();ds.save_as(out,enforce_file_format=True)
        arr,meta=read_upload(out.getvalue(),'x.dcm','CT');self.assertTrue(meta['is_hu']);self.assertEqual(arr[0,0],-1024)
        self.assertNotIn('private',json.dumps(meta))
        with self.assertRaises(ValueError):read_upload(out.getvalue(),'x.dcm','MRI')

    def test_audit_tampering_is_detected(self):
        r=self.start();self.assertTrue(self.app.store.verify()['valid'])
        with self.app.store.connect() as db:db.execute("UPDATE events SET data='{}' WHERE kind='run_created'")
        self.assertFalse(self.app.store.verify()['valid'])

    def test_export_has_provenance_and_no_pixels(self):
        r=self.start();payload=self.client.get(f"/api/runs/{r['id']}/export").json
        self.assertEqual(payload['schema'],'cardio-evidence/1');self.assertEqual(len(payload['run']['snapshot']['slice_sha256']),3)
        self.assertNotIn('PixelData',json.dumps(payload));self.assertTrue(payload['audit']['valid'])

    def test_persistence_readback(self):
        r=self.start();from cardio.store import Store
        self.assertEqual(Store(Path(self.temp.name)/'cardio.sqlite3').run(r['id'])['status'],'pending_review')

    def test_interrupted_run_is_blocked_without_new_tasks(self):
        r=self.app.agent.create(self.app.store.case('demo-ct-positive'),COMPLETE,'rules','demo',1)
        self.app.store.recover_interrupted();after=self.app.store.run(r['id'])
        self.assertEqual(after['status'],'blocked');self.assertEqual(self.app.store.tasks(r['id']),[])

    def test_stale_review_is_rejected(self):
        r=self.start();self.assertEqual(self.review(r,version=99).status_code,400);self.assertEqual(self.app.store.tasks(r['id']),[])


class PlannerTests(unittest.TestCase):
    def test_external_endpoint_is_rejected(self):
        for url in ['https://example.org/api/v1','http://localhost:13305/api/v1','http://127.0.0.1:1/api/v1?x=1','http://a@127.0.0.1/api/v1']:
            with self.subTest(url=url),self.assertRaises(ValueError):Planner({'lemonade_url':url})

    def test_minimal_prompt_and_schema_guard(self):
        planner=Planner({'planner_model':'local-test'})
        planner.local_model=Mock(return_value={'id':'local-test','downloaded':True,'recipe':'llamacpp'})
        planner.request=Mock(return_value={'id':'fixture','model':'local-test','usage':{'total_tokens':20},'choices':[{'message':{'content':'{"tool":"image_quality"}'}}]})
        run={'settings':{'planner':'lemonade'},'context':COMPLETE,'completed':[],'vision':{}}
        tool,record=planner.choose(run,'image_quality');self.assertEqual(tool,'image_quality');self.assertEqual(record['source'],'lemonade')
        payload=planner.request.call_args.args[1];content=payload['messages'][1]['content'];self.assertNotIn('62',content);self.assertNotIn('case_id',content)
        self.assertEqual(set(json.loads(content)),{'completed_tools','candidate_count','field_present','allowed_next'})
        planner.request.return_value={'choices':[{'message':{'content':'{"tool":"send_email"}'}}]}
        _,record=planner.choose(run,'image_quality');self.assertEqual(record['source'],'rules_fallback')

    def test_cloud_routing_and_missing_download_are_blocked(self):
        planner=Planner({})
        for entry in [{'id':'x','downloaded':True,'recipe':'cloud','cloud_provider':'remote'}, {'id':'x','downloaded':False,'recipe':'llamacpp'}, {'id':'x','recipe':'llamacpp'}]:
            planner.models=Mock(return_value={'data':[entry]})
            with self.subTest(entry=entry),self.assertRaises(ValueError):planner.local_model('x')


class ModelContractTests(unittest.TestCase):
    def test_nms_keeps_separate_candidates(self):
        boxes=np.array([[1,1,10,10],[2,2,10,10],[50,50,60,60]],float)
        self.assertEqual(nms(boxes,np.array([.9,.8,.7])),[0,2])

    def test_real_onnx_runtime_decode_nms_and_channel_order(self):
        import onnx
        from onnx import helper,TensorProto,numpy_helper
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'models').mkdir()
            # This is an explicitly untrained ONNX contract fixture, not a CAC model.
            # Output confidence is the maximum of previous-slice channel (0).
            axes=numpy_helper.from_array(np.array([0,2,3],np.int64),'axes')
            index=numpy_helper.from_array(np.array([0],np.int64),'idx')
            shape=numpy_helper.from_array(np.array([1,1,1],np.int64),'reshape')
            geometry=numpy_helper.from_array(np.array([[[250],[245],[10],[10]]],np.float32),'geometry')
            nodes=[helper.make_node('ReduceMax',['images','axes'],['means'],keepdims=0),helper.make_node('Gather',['means','idx'],['conf'],axis=0),helper.make_node('Reshape',['conf','reshape'],['score']),helper.make_node('Concat',['geometry','score'],['output'],axis=1)]
            graph=helper.make_graph(nodes,'fixture',[helper.make_tensor_value_info('images',TensorProto.FLOAT,[1,3,512,512])],[helper.make_tensor_value_info('output',TensorProto.FLOAT,[1,5,1])],[axes,index,shape,geometry])
            model=helper.make_model(graph,opset_imports=[helper.make_opsetid('',18)],ir_version=10);path=root/'models/fixture.onnx';onnx.save(model,path)
            manifest={'file':'fixture.onnx','name':'UNTRAINED CONTRACT FIXTURE','sha256':sha256(path),'input_size':512,'channels':'previous-center-next','output':'yolov8-raw-one-class','trained':False}
            (root/'models/detector.json').write_text(json.dumps(manifest))
            detector=Detector(root,{})
            arrays=[np.full((512,512),v,np.float32) for v in [204,128,30]]
            result=detector.detect(arrays,1,False)
            self.assertEqual(len(result['candidates']),1);self.assertAlmostEqual(result['candidates'][0]['confidence'],.8,places=4)
            self.assertEqual(result['candidates'][0]['xyxy'],[245.,240.,255.,250.]);self.assertFalse(result['trained'])
            self.assertEqual(result['input_indices'],[0,1,2])
            # Integrity checks run on each call, even when the session is cached.
            path.write_bytes(path.read_bytes()+b'tamper')
            with self.assertRaisesRegex(ValueError,'SHA256'):detector.detect(arrays,1,False)


if __name__=='__main__':unittest.main()
