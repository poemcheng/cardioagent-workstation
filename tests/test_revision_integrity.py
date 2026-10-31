"""Regression checks for confirmed review, current source and saved evidence."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from cardio.app import create_app
from cardio.store import canonical


ROOT = Path(__file__).resolve().parents[1]


class ReviewIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.app = create_app(self.data, synchronous=True, access_control=False)
        self.client = self.app.test_client()
        self.headers = {'X-CSRF-Token': self.client.get('/api/session').json['csrf']}

    def tearDown(self):
        self.app.pool.shutdown(wait=True)
        self.temp.cleanup()

    def post(self, path, body):
        return self.client.post(path, json=body, headers=self.headers)

    def execute(self, **changes):
        response = self.post('/api/analyses', {'case_id':'demo-ct-positive', 'mode':'demo', 'center':1, **changes})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def approve(self, result, **changes):
        return self.post('/api/analyses/'+result['id']+'/review',
                         {'version':result['version'], 'decision':'approve', 'review_confirmed':True, **changes})

    def change_pixel(self, result):
        path = self.data/self.app.store.case(result['case_id'])['slices'][0]
        image = np.load(path, allow_pickle=False)
        image[0,0] += 1
        np.save(path, image, allow_pickle=False)

    def test_server_requires_true_confirmation_and_keeps_unconfirmed_version(self):
        result = self.execute()
        for value in (None, False, 1, 'true'):
            response = self.approve(result, review_confirmed=value)
            self.assertEqual(response.status_code, 400, response.json)
            self.assertEqual(self.app.analysis.get(result['id'])['version'], result['version'])
        response = self.approve(result)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertTrue(response.json['review']['review_confirmed'])
        self.assertTrue(response.json['review']['source_integrity_checked']['source_current'])

    def test_changed_pixels_cannot_be_approved_but_can_be_rejected_with_reason(self):
        result = self.execute()
        self.change_pixel(result)
        response = self.approve(result)
        self.assertEqual(response.status_code, 400)
        current = self.client.get('/api/analyses/'+result['id']).json
        self.assertFalse(current['integrity']['source_current'])
        self.assertFalse(current['integrity']['approvable'])
        rejected = self.post('/api/analyses/'+result['id']+'/review',
                             {'version':result['version'], 'decision':'reject', 'reason':'來源已更新，重新分析'})
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.json['status'], 'rejected')

    def test_previously_approved_facts_cannot_transfer_after_source_change(self):
        result = self.execute()
        self.assertEqual(self.approve(result).status_code, 200)
        self.change_pixel(result)
        self.assertEqual(self.client.get('/api/analyses/'+result['id']+'/report-draft').status_code, 400)
        historical = self.client.get('/api/analyses/'+result['id']+'/export')
        self.assertEqual(historical.status_code, 200)
        self.assertFalse(historical.json['current_source_integrity']['source_current'])
        self.assertEqual(historical.json['analysis']['status'], 'approved')

    def test_geometry_change_alone_invalidates_report_compatibility(self):
        result = self.execute()
        case = self.app.store.case(result['case_id'])
        case['geometry'] = {'verified':False, 'units':'voxel', 'changed_metadata':True}
        with self.app.store.connect() as db:
            db.execute('UPDATE cases SET data=? WHERE id=?', (canonical(case), case['id']))
        self.assertEqual(self.approve(result).status_code, 400)
        status = self.client.get('/api/analyses/'+result['id']).json['integrity']
        self.assertFalse(status['geometry_current'])

    def test_removed_contributing_reference_blocks_approval(self):
        self.assertEqual(self.post('/api/analysis/phantom', {}).status_code, 200)
        result = self.execute(case_id='demo-volume-phantom', mode='inspect', threshold=120)
        self.assertTrue(result['comparison']['segmentation']['reference_sha256'])
        case = self.app.store.case(result['case_id'])
        case.pop('reference_mask')
        with self.app.store.connect() as db:
            db.execute('UPDATE cases SET data=? WHERE id=?', (canonical(case), case['id']))
        self.assertEqual(self.approve(result).status_code, 400)
        status = self.client.get('/api/analyses/'+result['id']).json['integrity']
        self.assertFalse(status['source_current'])

    def test_modified_current_analysis_is_rejected_before_render_review_or_export(self):
        result = self.execute()
        path = self.data/'analyses'/(result['id']+'.json')
        raw = json.loads(path.read_text())
        raw['recognition']['candidates'] = []
        path.write_text(json.dumps(raw))
        for suffix in ('', '/export', '/mesh.obj', '/report-draft'):
            self.assertEqual(self.client.get('/api/analyses/'+result['id']+suffix).status_code, 400)
        self.assertEqual(self.approve(result).status_code, 400)

    def test_windows_legacy_line_endings_preserve_audited_content(self):
        result = self.execute()
        path = self.data/'analyses'/(result['id']+'.json')
        path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
        self.assertEqual(self.client.get('/api/analyses/'+result['id']).status_code, 200)
        self.assertEqual(self.approve(result).status_code, 200)
        self.assertNotIn(b'\r\n', path.read_bytes())
        prior = self.data/'analyses/history'/result['id']/'1.json'
        prior.write_bytes(prior.read_bytes().replace(b'\n', b'\r\n'))
        self.assertEqual(self.client.get('/api/analyses/'+result['id']+'/export').status_code, 200)

    def test_modified_revision_cannot_be_mixed_into_valid_current_export(self):
        result = self.execute()
        self.assertEqual(self.approve(result).status_code, 200)
        path = self.data/'analyses/history'/result['id']/'1.json'
        raw = json.loads(path.read_text())
        raw['settings']['threshold'] = -1
        path.write_text(json.dumps(raw))
        self.assertEqual(self.client.get('/api/analyses/'+result['id']).status_code, 200)
        self.assertEqual(self.client.get('/api/analyses/'+result['id']+'/export').status_code, 400)

    def test_missing_requested_detection_or_segmentation_never_approves(self):
        for changes in ({'mode':'model'}, {'segmentation_mode':'registered_model'}):
            with self.subTest(changes=changes):
                result = self.execute(**changes)
                self.assertFalse(result['integrity']['approvable'])
                self.assertEqual(self.approve(result).status_code, 400)

    def test_acute_presentation_and_review_do_not_read_image_sources(self):
        run = self.post('/api/runs', {'case_id':'demo-ct-positive', 'mode':'demo', 'center':1,
            'context':{'age':62,'acute':True,'history_complete':True,'consent':True}}).json
        with patch.object(self.app.analysis, '_source_path', side_effect=AssertionError('acute must skip images')) as load:
            result = self.execute(run_id=run['id'])
            self.assertFalse(result['integrity']['source_checked'])
            self.assertEqual(self.approve(result).status_code, 400)
            load.assert_not_called()

    def test_new_acute_flag_after_analysis_blocks_old_approval(self):
        run = self.post('/api/runs', {'case_id':'demo-ct-positive', 'mode':'demo', 'center':1,
            'context':{'age':62,'acute':False,'history_complete':True,'consent':True}}).json
        result = self.execute(run_id=run['id'])
        current = self.app.store.run(run['id'])
        current['context']['acute'] = True
        self.app.store.save_run(current, 'test_context_changed')
        with patch.object(self.app.analysis, '_source_path', side_effect=AssertionError('acute must skip images')):
            self.assertEqual(self.approve(result).status_code, 400)

    def test_unknown_fields_are_rejected_without_creating_an_analysis(self):
        response = self.post('/api/analyses', {'case_id':'demo-ct-positive', 'segmentation_mdoe':'registered_model'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.app.analysis.list(), [])


class PreflightAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('patch_preflight', ROOT/'scripts/demo_preflight.py')
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_missing_bundled_reference_prevents_false_integrated_ready(self):
        with patch('cardio.reference_labels.verify_reference_assets', side_effect=ValueError('label source missing')):
            result = self.module.inspect(probe_service=False)
        self.assertTrue(result['core_demo_ready'])
        self.assertFalse(result['provided_mri_reference']['verified_assets'])
        self.assertFalse(result['workflow_demo_ready'])

    def test_model_inventory_uses_the_same_purpose_allowlist_as_execution(self):
        models = {'data':[
            {'id':'local-chat','recipe':'llamacpp','downloaded':True},
            {'id':'local-voice','recipe':'whispercpp','downloaded':True},
            {'id':'unknown-engine','recipe':'unknown','downloaded':True},
            {'id':'remote','recipe':'llamacpp','downloaded':True,'cloud_provider':'remote'},
            {'id':'pending','recipe':'flm','downloaded':False},
        ]}
        with patch('cardio.planner.Planner.models', return_value=models):
            result = self.module.inspect(probe_service=True)
        actual = {(m['id'],m['purpose']) for m in result['lemonade']['downloaded_local_models']}
        self.assertEqual(actual, {('local-chat','chat'),('local-voice','speech')})
        self.assertTrue(result['provided_mri_reference']['verified_assets'])
        self.assertFalse(result['npu_execution_verified'])


if __name__ == '__main__':
    unittest.main()
