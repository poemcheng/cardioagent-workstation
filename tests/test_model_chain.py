"""Real ORT and loopback protocol fixtures; no trained model or AMD claims."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from cardio.app import create_app
from cardio.imaging import Detector, sha256
from cardio.planner import Planner
from cardio.store import canonical
from scripts import accept_model_chain

CONTEXT = {'age':62, 'acute':False, 'history_complete':True, 'consent':True}


def contract_model(root):
    import onnx
    from onnx import helper, TensorProto, numpy_helper
    (root/'models').mkdir()
    # Two overlapping boxes (IoU=2/3) and an entirely out-of-image box.
    predictions = np.array([[[100,120,-20],[100,100,100],[100,100,10],
                             [100,100,10],[.9,.8,.99]]], dtype=np.float32)
    node = helper.make_node('Constant', [], ['predictions'], value=numpy_helper.from_array(predictions))
    graph = helper.make_graph([node], 'UNTRAINED CONTRACT TEST ONLY',
        [helper.make_tensor_value_info('images', TensorProto.FLOAT, [1,3,512,512])],
        [helper.make_tensor_value_info('predictions', TensorProto.FLOAT, [1,5,3])])
    path = root/'models/fixture.onnx'
    onnx.save(helper.make_model(graph, opset_imports=[helper.make_opsetid('',18)], ir_version=10), path)
    manifest = {'file':'fixture.onnx', 'name':'UNTRAINED PROTOCOL TEST ONLY', 'sha256':sha256(path),
        'input_size':512, 'channels':'previous-center-next', 'output':'yolov8-raw-one-class',
        'trained':False, 'provenance':'generated ONNX contract fixture, never clinical weights',
        'postprocessing':{'confidence':.25, 'nms_iou':.5}}
    (root/'models/detector.json').write_text(json.dumps(manifest), encoding='utf-8')
    return manifest


class LoopbackFixture:
    def __init__(self):
        self.calls, self.fault = [], None
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def send(self, value):
                raw = json.dumps(value).encode()
                self.send_response(200);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            def do_GET(self):
                self.send({'data':[{'id':'protocol-test', 'recipe':'llamacpp', 'downloaded':True}]})
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fixture.calls.append(body)
                state = json.loads(body['messages'][1]['content'])
                result = {'id':str(len(fixture.calls)) if fixture.fault != 'duplicate' else 'same-id',
                    'model':'other-model' if fixture.fault == 'model' else body['model'],
                    'choices':[{'message':{'content':json.dumps({'tool':state['allowed_next'][0]})}}],
                    'usage':{'prompt_tokens':12,'completion_tokens':8,'total_tokens':20}}
                if fixture.fault == 'usage': result.pop('usage')
                self.send(result)
        self.server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}/v1'

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join()


class ModelChainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.root = Path(self.temp.name)
        self.manifest = contract_model(self.root)
        self.service = LoopbackFixture()
        def factory(*args, **kwargs):
            app = create_app(*args, **kwargs)
            app.detector.root = self.root
            app.agent.planner.base = self.service.base
            return app
        self.factory = patch.object(accept_model_chain, 'create_app', side_effect=factory)
        self.factory.start()

    def tearDown(self):
        self.factory.stop();self.service.close();self.temp.cleanup()

    def declared_fixture(self):
        # Intentionally exercise the declaration gate. This does not train the
        # test graph, and these files are disposable and never shipped as weights.
        self.manifest['trained'] = True
        (self.root/'models/detector.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def execute(self, name='result', **overrides):
        return accept_model_chain.run(self.root/name, 'protocol-test', self.manifest['sha256'],
                                     **({'synthetic':True}|overrides))

    def test_real_onnx_and_six_loopback_protocol_responses_leave_human_gate_closed(self):
        self.declared_fixture();report = self.execute()
        self.assertTrue(report['passed_requested_scope'], report)
        self.assertFalse(report['research_input_chain_verified'])
        self.assertFalse(report['amd_hardware_execution_verified'])
        self.assertFalse(report['human_review_performed'])
        self.assertEqual(len(self.service.calls),6)
        for request in self.service.calls:
            state = json.loads(request['messages'][1]['content'])
            self.assertEqual(set(state), {'completed_tools','candidate_count','field_present','allowed_next'})
            self.assertNotIn('age', state)
        evidence = json.loads((self.root/'result/run-evidence.json').read_text())
        self.assertEqual(evidence['run']['status'],'pending_review')
        self.assertEqual(evidence['tasks'],[])
        vision = evidence['run']['vision']
        self.assertTrue(vision['inference_executed'])
        self.assertEqual(vision['postprocessing']['nms_iou'],.5)
        self.assertEqual(len(vision['candidates']),1)
        self.assertEqual(len({p['plan']['response_id'] for p in evidence['run']['tool_log']}),6)
        self.assertTrue(all(p['plan']['request_trace']['success'] for p in evidence['run']['tool_log']))
        for line in (self.root/'result/SHA256SUMS.txt').read_text().splitlines():
            digest, name = line.split('  ')
            self.assertEqual(sha256(self.root/'result'/name),digest)
        self.assertTrue((self.root/'result/audit-events.json').is_file())
        self.assertEqual(list((self.root/'result').glob('*.npy')),[])

    def test_model_mismatch_and_duplicate_ids_and_missing_usage_fail_strict_scope(self):
        self.declared_fixture()
        for fault in ('model','duplicate','usage'):
            with self.subTest(fault=fault):
                self.service.fault = fault
                report = self.execute(fault)
                self.assertFalse(report['passed_requested_scope'])
                details = report['checks']['chain']['result']
                self.assertEqual(details['fallback_responses'],6 if fault=='model' else 0)
                if fault=='duplicate':self.assertFalse(details['checks']['six_distinct_response_ids'])
                if fault=='usage':self.assertFalse(details['checks']['token_usage_recorded'])

    def test_missing_weights_or_undeclared_training_prevent_all_chat_calls(self):
        report = self.execute()
        self.assertFalse(report['checks']['vision_model']['passed'])
        self.assertEqual(self.service.calls,[])
        (self.root/'models/detector.json').unlink()
        report = self.execute('missing')
        self.assertFalse(report['passed_requested_scope'])
        self.assertEqual(self.service.calls,[])

    def test_wrong_expected_weights_prevent_execution(self):
        self.declared_fixture()
        report = accept_model_chain.run(self.root/'wrong', 'protocol-test', '0'*64, synthetic=True)
        self.assertFalse(report['checks']['vision_model']['passed'])
        self.assertEqual(self.service.calls,[])

    def test_ordered_authorized_png_input_is_imported_without_original_filenames(self):
        self.declared_fixture();images = []
        for index in range(3):
            path = self.root/f'private-patient-{index}.png'
            Image.fromarray(np.tile(np.arange(512,dtype=np.uint8),(512,1))).save(path)
            images.append(path)
        report = self.execute(synthetic=False, images=images, context=CONTEXT, authorized=True)
        self.assertTrue(report['passed_requested_scope'],report)
        self.assertTrue(report['research_input_chain_verified'])
        self.assertEqual(report['ordered_upload_sha256'],[sha256(p) for p in images])
        text = ''.join(p.read_text() for p in (self.root/'result').glob('*.json'))
        self.assertNotIn('private-patient',text)

    def test_missing_authorization_and_acute_context_do_not_create_app_or_call_models(self):
        for changes in ({'authorized':False}, {'context':CONTEXT|{'acute':True}}, {'context':CONTEXT|{'history_complete':False}}):
            with self.subTest(changes=changes):
                with patch.object(accept_model_chain, 'create_app') as factory:
                    report = self.execute(uuid_name(changes), **({'synthetic':False, 'images':[Path('not-read.png')]*3,
                        'context':CONTEXT, 'authorized':True}|changes))
                    self.assertFalse(report['passed_requested_scope']);factory.assert_not_called()
        self.assertEqual(self.service.calls,[])

    def test_existing_output_is_never_overwritten(self):
        self.execute()
        original = (self.root/'result/acceptance.json').read_bytes()
        with self.assertRaises(FileExistsError):self.execute()
        self.assertEqual(original,(self.root/'result/acceptance.json').read_bytes())


def uuid_name(value):
    import hashlib
    return hashlib.sha256(canonical(value).encode()).hexdigest()[:10]


class InferenceIntegrityTests(unittest.TestCase):
    def test_nms_configuration_changes_actual_outputs_and_rejects_nan_and_collapsed_boxes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);manifest=contract_model(root)
            detector=Detector(root,{})
            arrays=[np.tile(np.arange(512,dtype=np.float32)%256,(512,1)) for _ in range(3)]
            self.assertEqual(len(detector.detect(arrays,1,False)['candidates']),1)
            manifest['postprocessing']['nms_iou']=.7
            (root/'models/detector.json').write_text(json.dumps(manifest))
            result=detector.detect(arrays,1,False)
            self.assertEqual(len(result['candidates']),2)
            self.assertEqual(result['postprocessing']['nms_iou'],.7)
            arrays[0][1,1]=float('nan')
            with self.assertRaisesRegex(ValueError,'0–255'):detector.detect(arrays,1,False)
            arrays[0][1,1]=1
            for invalid in (True,float('nan'),-1,2):
                manifest['postprocessing']['nms_iou']=invalid
                (root/'models/detector.json').write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError,'NMS'):detector.detect(arrays,1,False)

    def test_planner_rejects_blank_response_id_and_wrong_returned_model(self):
        planner=Planner({'planner_model':'selected'})
        planner.local_model=Mock(return_value={'id':'selected','recipe':'llamacpp'})
        response={'id':'id','model':'selected','choices':[{'message':{'content':'{"tool":"image_quality"}'}}]}
        run={'settings':{'planner':'lemonade'},'context':CONTEXT,'completed':[]}
        for change in ({'id':''},{'model':'wrong'},{'id':None}):
            planner.request=Mock(return_value=response|change)
            self.assertEqual(planner.choose(run,'image_quality')[1]['source'],'rules_fallback')

    def test_source_changes_block_resume_approval_and_execution_but_allow_acute_escalation(self):
        with tempfile.TemporaryDirectory() as temp:
            app=create_app(temp,synchronous=True,access_control=False)
            try:
                case=app.store.case('demo-ct-positive')
                waiting=app.agent.create(case,CONTEXT|{'age':None},'rules','demo',1)
                app.agent.execute(waiting['id'])
                ready=app.agent.create(case,CONTEXT,'rules','demo',1);app.agent.execute(ready['id'])
                queued=app.agent.create(case,CONTEXT,'rules','demo',1)
                source=Path(temp)/case['slices'][0]
                original=source.read_bytes();source.write_bytes(original+b'changed')
                with self.assertRaisesRegex(ValueError,'來源'):app.agent.resume(waiting['id'],CONTEXT,1)
                with self.assertRaisesRegex(ValueError,'來源'):app.agent.review(ready['id'],'approve','reviewer','',1)
                self.assertEqual(app.store.tasks(ready['id']),[])
                app.agent.execute(queued['id']);self.assertEqual(app.store.run(queued['id'])['status'],'blocked')
                urgent=app.agent.resume(waiting['id'],CONTEXT|{'acute':True},1)
                with patch.object(app.agent,'verify_source',side_effect=AssertionError('no image reads')):
                    app.agent.execute(urgent['id'])
                self.assertEqual(app.store.run(urgent['id'])['status'],'emergency')
            finally:app.pool.shutdown(wait=True)


if __name__=='__main__':unittest.main()
