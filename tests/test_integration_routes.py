"""Real Flask-route integration; actual pixels, persistence and human gates.

These checks do not load clinical weights or infer AMD hardware capability.
"""
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np
from PIL import Image

from cardio.app import create_app
from cardio.discussion import REVIEW_FLAGS
from cardio.imaging import display_pixels


PASSWORD = 'Synthetic-integration-test-7391'
COMPLETE_DISCUSSION = {'acute': False, **{key: True for key in REVIEW_FLAGS}}


class IntegratedRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.app = create_app(self.data, synchronous=True)
        self.client = self.app.test_client()
        csrf = self.client.get('/api/session').json['csrf']
        response = self.client.post('/api/auth/setup',
                                    json={'username': 'integration-admin', 'password': PASSWORD},
                                    headers={'X-CSRF-Token': csrf})
        self.assertEqual(response.status_code, 200, response.json)
        self.headers = {'X-CSRF-Token': self.client.get('/api/session').json['csrf']}

    def tearDown(self):
        self.app.pool.shutdown(wait=True)
        self.temp.cleanup()

    def load_phantom(self):
        response = self.client.post('/api/analysis/phantom', json={}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def execute(self, case_id='demo-volume-phantom', **changes):
        if case_id == 'demo-volume-phantom':
            self.load_phantom()
        body = {'case_id': case_id, 'mode': 'demo' if case_id.startswith('demo-') else 'inspect',
                'center': 16 if case_id == 'demo-volume-phantom' else 1,
                'threshold': 120, 'use_llm': False, **changes}
        response = self.client.post('/api/analyses', json=body, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def review(self, result, decision='approve', **changes):
        return self.client.post('/api/analyses/' + result['id'] + '/review',
                                json={'version': result['version'], 'decision': decision,
                                      'reason': '本機合成流程覆核', 'review_confirmed': decision=='approve', **changes}, headers=self.headers)

    def test_analysis_pages_and_all_api_reads_require_a_session(self):
        anonymous = self.app.test_client()
        for path in ('/api/analysis/status', '/api/analyses', '/api/analyses/' + 'a'*32,
                     '/api/analyses/' + 'a'*32 + '/export',
                     '/api/analyses/' + 'a'*32 + '/mesh.obj',
                     '/api/analyses/' + 'a'*32 + '/slices/0?view=mask',
                     '/api/analyses/' + 'a'*32 + '/report-draft'):
            self.assertEqual(anonymous.get(path).status_code, 401, path)
        self.assertEqual(anonymous.get('/analysis').status_code, 302)
        self.assertEqual(self.client.post('/api/analysis/phantom', json={}).status_code, 403)
        self.assertEqual(self.client.post('/api/analyses', json={'case_id': 'demo-ct-positive'}).status_code, 403)
        status = self.client.get('/api/analysis/status')
        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.json['clinical_accuracy_verified'])
        self.assertFalse(status.json['amd_hardware_verified'])
        self.assertFalse(status.json['npu_execution_verified'])

    def test_mutation_permissions_and_authenticated_actor_are_enforced(self):
        for username, role in (('integration-viewer', 'viewer'),
                               ('integration-coordinator', 'coordinator'),
                               ('integration-reader', 'radiologist')):
            self.app.auth.add(username, PASSWORD, role)
            client = self.app.test_client()
            token = client.get('/api/session').json['csrf']
            login = client.post('/api/auth/login', json={'username': username, 'password': PASSWORD},
                                headers={'X-CSRF-Token': token})
            self.assertEqual(login.status_code, 200)
            headers = {'X-CSRF-Token': client.get('/api/session').json['csrf']}
            self.assertEqual(client.get('/api/analyses').status_code, 200)
            self.assertEqual(client.get('/api/analysis/status').status_code, 200)
            if role != 'radiologist':
                for path in ('/api/analysis/phantom', '/api/analyses',
                             '/api/analyses/' + 'a'*32 + '/discussion',
                             '/api/analyses/' + 'a'*32 + '/review'):
                    self.assertEqual(client.post(path, json={}, headers=headers).status_code, 403, (role,path))
            else:
                self.assertEqual(client.post('/api/analysis/phantom', json={}, headers=headers).status_code, 200)
                response = client.post('/api/analyses', headers=headers,
                    json={'case_id': 'demo-ct-positive', 'mode': 'demo', 'center': 1, 'actor': 'forged-admin'})
                self.assertEqual(response.status_code, 400, response.json)
                response = client.post('/api/analyses', headers=headers,
                    json={'case_id': 'demo-ct-positive', 'mode': 'demo', 'center': 1})
                self.assertEqual(response.status_code, 201, response.json)
                self.assertEqual(response.json['actor'], username)

    def test_phantom_is_explicit_and_idempotent_without_duplicate_studies(self):
        self.assertEqual(len(self.client.get('/api/hospital/studies').json), 3)
        first, second = self.load_phantom(), self.load_phantom()
        studies = self.client.get('/api/hospital/studies').json
        self.assertEqual(len(studies), 4)
        self.assertEqual(first['study']['id'], second['study']['id'])
        self.assertEqual(first['case_id'], 'demo-volume-phantom')
        self.assertEqual(sum(s['case_id'] == first['case_id'] for s in studies), 1)
        case = self.client.get('/api/cases/' + first['case_id']).json
        self.assertTrue(case['synthetic'])
        self.assertEqual(case['slice_count'], 32)
        self.assertEqual(case['geometry']['coordinate_system'], 'synthetic_world')
        self.assertTrue(case['geometry']['verified'])

    def test_real_execution_attaches_initial_discussion_and_measured_geometry(self):
        result = self.execute()
        self.assertEqual(result['version'], 2)
        self.assertEqual(result['status'], 'pending_review')
        self.assertEqual(result['source']['shape_zyx'], [32,96,96])
        self.assertEqual(len(result['source']['slice_sha256']), 32)
        self.assertEqual(result['reconstruction']['statistics']['foreground_voxels'], 28736)
        self.assertEqual(result['reconstruction']['statistics']['physical_volume_mm3'], 89800.)
        self.assertEqual(result['reconstruction']['mesh']['units'], 'mm')
        self.assertGreater(len(result['reconstruction']['mesh']['faces']), 0)
        self.assertEqual(result['comparison']['segmentation']['dice'], 1.)
        self.assertEqual(result['comparison']['segmentation']['scope'], 'synthetic_fixture_only_not_clinical_accuracy')
        self.assertIsNone(result['discussion']['recommendation']['procedure'])
        self.assertFalse(result['discussion']['surgery_planning_ready'])
        self.assertFalse(result['discussion']['automatic_orders_created'])
        self.assertEqual(result['language']['source'], 'rules')
        self.assertFalse(result['explanation']['saliency_available'])
        self.assertTrue(all(candidate['confidence'] is None for candidate in result['recognition']['candidates']))
        self.assertTrue(self.app.store.verify()['valid'])
        listed = self.client.get('/api/analyses?case_id=demo-volume-phantom').json
        self.assertEqual([entry['id'] for entry in listed], [result['id']])
        self.assertEqual(self.client.get('/api/analyses/' + result['id']).json['version'], 2)

    def test_original_mask_and_overlay_are_actual_source_bound_png_pixels(self):
        result = self.execute()
        index = 8  # Not the candidate-overlay centre, isolating the mask alpha blend.
        prefix = '/api/analyses/' + result['id'] + '/slices/' + str(index)
        images = {}
        for view in ('original','mask','overlay'):
            response = self.client.get(prefix + '?view=' + view)
            self.assertEqual(response.status_code, 200, response.json)
            self.assertEqual(response.mimetype, 'image/png')
            images[view] = np.asarray(Image.open(io.BytesIO(response.data)))
            self.assertEqual(images[view].shape, (96,96,3))
        case = self.app.store.case(result['case_id'])
        pixels = np.load(self.data / case['slices'][index], allow_pickle=False)
        mask = pixels >= 120
        expected_original = np.repeat(display_pixels(pixels)[...,None], 3, axis=2)
        np.testing.assert_array_equal(images['original'], expected_original)
        np.testing.assert_array_equal(images['mask'][...,0], mask.astype(np.uint8)*255)
        np.testing.assert_array_equal(images['mask'][...,0], images['mask'][...,1])
        np.testing.assert_array_equal(images['overlay'][~mask], images['original'][~mask])
        expected_overlay = (.60*images['original'][mask] + .40*np.asarray([30,190,145])).astype(np.uint8)
        np.testing.assert_array_equal(images['overlay'][mask], expected_overlay)
        self.assertEqual(self.client.get(prefix+'?view=unrecognized').status_code, 400)
        self.assertEqual(self.client.get('/api/analyses/'+result['id']+'/slices/99').status_code, 400)

    def test_source_hash_change_blocks_original_and_mask_overlay(self):
        result = self.execute()
        case = self.app.store.case(result['case_id'])
        path = self.data / case['slices'][8]
        array = np.load(path, allow_pickle=False)
        array[0,0] += 1
        np.save(path, array, allow_pickle=False)
        for view in ('original','mask','overlay'):
            response = self.client.get('/api/analyses/'+result['id']+'/slices/8?view='+view)
            self.assertEqual(response.status_code, 400, view)
            self.assertIn('來源已變更', response.json['error'])

    def test_mask_hash_change_is_rejected_instead_of_rendered(self):
        result = self.execute()
        path = self.data / 'analyses' / (result['id'] + '.mask.npy')
        mask = np.load(path, allow_pickle=False)
        mask[8,0,0] = not bool(mask[8,0,0])
        np.save(path, mask, allow_pickle=False)
        response = self.client.get('/api/analyses/'+result['id']+'/slices/8?view=mask')
        self.assertEqual(response.status_code, 400)
        self.assertIn('雜湊不符', response.json['error'])

    def test_mesh_and_evidence_exports_retain_units_sources_and_audit(self):
        result = self.execute()
        for extension in ('obj','stl'):
            response = self.client.get('/api/analyses/'+result['id']+'/mesh.'+extension)
            self.assertEqual(response.status_code, 200)
            self.assertIn('mm.'+extension, response.headers['Content-Disposition'])
            self.assertIn('mm', response.get_data(as_text=True))
            self.assertNotIn('nan', response.get_data(as_text=True).lower())
        invalid = self.client.get('/api/analyses/'+result['id']+'/mesh.unrecognized')
        self.assertEqual(invalid.status_code, 400)
        exported = self.client.get('/api/analyses/'+result['id']+'/export')
        self.assertEqual(exported.status_code, 200)
        payload = exported.json
        self.assertEqual(payload['schema'], 'cardio-integrated-evidence/1')
        self.assertEqual(payload['analysis']['id'], result['id'])
        self.assertEqual(payload['analysis']['version'], 2)
        self.assertEqual(payload['revisions'][0]['version'], 1)
        self.assertTrue(payload['audit']['valid'])
        self.assertTrue(payload['analysis']['discussion']['sources'])
        self.assertNotIn('PixelData', json.dumps(payload))

    def test_report_facts_require_analysis_approval_and_never_save_report(self):
        result = self.execute()
        study = next(s for s in self.client.get('/api/hospital/studies').json if s['case_id']==result['case_id'])
        path = '/api/analyses/'+result['id']+'/report-draft'
        self.assertEqual(self.client.get(path).status_code, 400)
        self.assertIsNone(self.client.get('/api/hospital/studies/'+study['id']).json['report'])
        approved = self.review(result, actor='forged-reviewer')
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertEqual(approved.json['review']['actor'], 'integration-admin')
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.json)
        draft = response.json
        self.assertEqual(draft['schema'], 'cardio-analysis-report-draft/1')
        self.assertEqual(draft['engine']['source'], 'reviewed_analysis_facts')
        self.assertEqual(set(draft['fields']), {'indication','technique','comparison','findings','impression','limitations'})
        self.assertIn('89800.000', draft['fields']['findings'])
        self.assertIn('合成設計間距', draft['fields']['findings'])
        self.assertIn('待影像覆核者', draft['fields']['impression'])
        self.assertIsNone(self.client.get('/api/hospital/studies/'+study['id']).json['report'])

    def test_discussion_flags_can_prepare_mdt_but_never_choose_procedure(self):
        result = self.execute()
        response = self.client.post('/api/analyses/'+result['id']+'/discussion', headers=self.headers,
            json={'version':result['version'], 'context':COMPLETE_DISCUSSION | {'clinician_note':'人工研究備註。'}})
        self.assertEqual(response.status_code, 200, response.json)
        updated = response.json
        self.assertEqual(updated['version'], 3)
        discussion = updated['discussion']
        self.assertEqual(discussion['status'], 'ready_for_mdt')
        self.assertIsNone(discussion['recommendation']['procedure'])
        self.assertFalse(discussion['surgery_planning_ready'])
        self.assertFalse(discussion['automatic_orders_created'])
        self.assertEqual(discussion['clinical_note']['origin'], 'clinician_entered')
        self.assertFalse(discussion['clinical_note']['verified'])
        self.assertEqual(len(discussion['options']), 3)
        self.assertTrue(all(option['scope'] for option in discussion['options']))
        self.assertTrue(self.app.store.verify()['valid'])

    def test_discussion_and_review_enforce_versions_and_finality(self):
        result = self.execute()
        path = '/api/analyses/'+result['id']+'/discussion'
        for version in (result['version']-1, True):
            response = self.client.post(path, json={'version':version,'context':COMPLETE_DISCUSSION}, headers=self.headers)
            self.assertEqual(response.status_code, 400)
        changed = self.client.post(path, json={'version':result['version'],'context':COMPLETE_DISCUSSION}, headers=self.headers)
        self.assertEqual(changed.status_code, 200, changed.json)
        self.assertEqual(self.review(result).status_code, 400)
        approved = self.review(changed.json)
        self.assertEqual(approved.status_code, 200, approved.json)
        self.assertEqual(self.review(approved.json).status_code, 400)
        response = self.client.post(path, json={'version':approved.json['version'],'context':COMPLETE_DISCUSSION}, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        payload = self.client.get('/api/analyses/'+result['id']+'/export').json
        self.assertEqual([r['version'] for r in payload['revisions']], [1,2,3])

    def test_rejection_requires_reason_and_is_terminal(self):
        result = self.execute()
        self.assertEqual(self.review(result, 'reject', reason='').status_code, 400)
        rejected = self.review(result, 'reject')
        self.assertEqual(rejected.status_code, 200, rejected.json)
        self.assertEqual(rejected.json['status'], 'rejected')
        self.assertEqual(self.review(rejected.json).status_code, 400)
        self.assertEqual(self.client.get('/api/analyses/'+result['id']+'/report-draft').status_code, 400)
        discussion = self.client.post('/api/analyses/'+result['id']+'/discussion', headers=self.headers,
            json={'version':rejected.json['version'],'context':COMPLETE_DISCUSSION})
        self.assertEqual(discussion.status_code, 400)
        self.assertIsNone(rejected.json['discussion']['recommendation']['procedure'])

    def test_mri_inspection_is_measured_without_ct_model_or_physical_volume(self):
        self.app.detector.detect = Mock(side_effect=AssertionError('CT model must not run on MRI inspection'))
        result = self.execute('research-mri-01', mode='inspect', threshold=128)
        self.assertEqual(result['source']['shape_zyx'], [8,512,428])
        self.assertFalse(result['source']['synthetic'])
        self.assertEqual(result['recognition']['scope'], 'image_inspection_only')
        self.assertFalse(result['recognition']['inference_executed'])
        self.assertEqual(result['recognition']['candidates'], [])
        self.assertIsNone(result['reconstruction']['statistics']['physical_volume_mm3'])
        self.assertEqual(result['reconstruction']['mesh']['units'], 'voxel')
        self.assertTrue(result['comparison']['segmentation']['available'])
        self.assertEqual(result['comparison']['segmentation']['scope'], 'provided_research_label_agreement_not_clinical_accuracy')
        self.assertAlmostEqual(result['comparison']['segmentation']['dice'], 0.1814946331797905, places=10)
        self.assertFalse(result['discussion']['surgery_planning_ready'])
        self.app.detector.detect.assert_not_called()

    def test_two_dimensional_mask_remains_viewable_when_volume_reconstruction_is_blocked(self):
        pixels = np.arange(32*32, dtype=np.uint16).reshape(32,32).astype(np.uint8)
        raw = io.BytesIO()
        Image.fromarray(pixels).save(raw, format='PNG')
        uploaded = self.client.post('/api/cases', headers=self.headers,
            data={'modality':'MRI', 'images':(io.BytesIO(raw.getvalue()),'deidentified-export.png')})
        self.assertEqual(uploaded.status_code, 201, uploaded.json)
        result = self.execute(uploaded.json['id'], mode='inspect', center=0, threshold=128)
        self.assertIsNone(result['reconstruction'])
        self.assertIsNotNone(result['segmentation'])
        self.assertEqual(result['source']['shape_zyx'], [1,32,32])
        response = self.client.get('/api/analyses/'+result['id']+'/slices/0?view=mask')
        self.assertEqual(response.status_code, 200, response.json)
        actual = np.asarray(Image.open(io.BytesIO(response.data)))
        np.testing.assert_array_equal(actual[...,0], (pixels>=128).astype(np.uint8)*255)
        self.assertEqual(self.client.get('/api/analyses/'+result['id']+'/mesh.obj').status_code, 400)

    def test_busy_optional_language_stage_does_not_block_linked_acute_analysis(self):
        # Genuine concurrent Flask requests; only the optional language-stage
        # wait is controlled. All workflow records and replies remain real.
        worker_client = self.app.test_client()
        token = worker_client.get('/api/session').json['csrf']
        login = worker_client.post('/api/auth/login',
            json={'username':'integration-admin','password':PASSWORD},
            headers={'X-CSRF-Token':token})
        self.assertEqual(login.status_code, 200)
        worker_headers = {'X-CSRF-Token':worker_client.get('/api/session').json['csrf']}
        entered, release, completed = threading.Event(), threading.Event(), threading.Event()
        responses, errors = [], []
        original_language = self.app.analysis._language
        def waiting_language(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise AssertionError('Test did not release the language stage')
            return original_language(*args, **kwargs)
        self.app.analysis._language = waiting_language
        def ordinary_request():
            try:
                responses.append(worker_client.post('/api/analyses', headers=worker_headers,
                    json={'case_id':'demo-ct-positive','mode':'inspect','center':1,'threshold':240}))
            except Exception as exc:
                errors.append(exc)
            finally:
                completed.set()
        thread = threading.Thread(target=ordinary_request, daemon=True)
        thread.start()
        try:
            self.assertTrue(entered.wait(5), 'Ordinary analysis must reach its optional language wait')
            busy = self.client.post('/api/analyses', headers=self.headers,
                json={'case_id':'demo-ct-positive','mode':'inspect','center':1})
            self.assertEqual(busy.status_code, 409, 'Another ordinary analysis remains serialized')
            context = {'age':62,'acute':True,'history_complete':True,'consent':True}
            run = self.client.post('/api/runs', headers=self.headers,
                json={'case_id':'demo-ct-positive','mode':'demo','planner':'rules','center':1,'context':context})
            self.assertEqual(run.status_code, 202, run.json)
            run_id = run.json['id']
            self.assertEqual(self.client.get('/api/runs/'+run_id).json['status'], 'emergency')
            # Ordinary input I/O already finished; acute analysis must not call
            # it anew or wait for model computation.
            self.app.analysis._load = Mock(side_effect=AssertionError('Acute record must not load image arrays'))
            urgent = self.client.post('/api/analyses', headers=self.headers,
                json={'case_id':'demo-ct-positive','mode':'inspect','center':1,'run_id':run_id})
            self.assertEqual(urgent.status_code, 201, urgent.json)
            self.assertFalse(completed.is_set(), 'Acute reply must complete before releasing the ordinary model wait')
            result = urgent.json
            self.assertEqual(result['version'], 2)
            self.assertTrue(result['run_snapshot']['acute'])
            self.assertEqual(result['discussion']['status'], 'emergency')
            self.assertIsNone(result['discussion']['recommendation']['procedure'])
            self.assertIsNone(result['reconstruction'])
            self.assertEqual(result['source']['slice_sha256'], [])
            self.assertTrue(all(stage['status']=='blocked' for stage in result['stages'] if stage['key']!='discussion'))
            self.app.analysis._load.assert_not_called()
            self.assertEqual(self.client.get('/api/runs/'+run_id).json['tasks'], [])
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0].status_code, 201, responses[0].json)
        self.assertTrue(self.app.store.verify()['valid'])

    def test_unavailable_segmentation_cannot_return_original_as_a_mask(self):
        result = self.execute(segmentation_mode='registered_model')
        self.assertIsNone(result['segmentation'])
        self.assertIsNone(result['reconstruction'])
        segmentation_stage = next(s for s in result['stages'] if s['key']=='segmentation')
        self.assertEqual(segmentation_stage['status'], 'blocked')
        response = self.client.get('/api/analyses/'+result['id']+'/slices/8?view=mask')
        self.assertEqual(response.status_code, 400, 'A missing segmentation must not produce an original image labelled as a mask')
        self.assertEqual(self.client.get('/api/analyses/'+result['id']+'/mesh.obj').status_code, 400)


if __name__ == '__main__':
    unittest.main()
