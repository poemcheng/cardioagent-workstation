"""Actual source-bound preparation jobs; no clinical or AMD performance claims."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cardio.app import create_app
from cardio.resources import ModelGate
from cardio.workspace import cost_scenario


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.temp.name), synchronous=True)
        self.client = self.app.test_client()
        csrf = self.client.get('/api/session').json['csrf']
        r = self.client.post('/api/auth/setup', json={'username': 'preparation-admin', 'password': 'Preparation-test-7391'}, headers={'X-CSRF-Token': csrf})
        self.assertEqual(r.status_code, 200)
        self.headers = {'X-CSRF-Token': self.client.get('/api/session').json['csrf']}

    def tearDown(self):
        self.app.pool.shutdown(wait=True)
        self.temp.cleanup()

    def body(self, **changes):
        return {'case_id': 'research-mri-01', 'goal': '整理研究影像與提供標籤', 'question': '來源與三維資料是否相符？',
                'context': {'acute': False, 'history_complete': True, 'consent': True}, 'settings': {}, **changes}

    def create(self, **changes):
        r = self.client.post('/api/workspace/orders', json=self.body(**changes), headers=self.headers)
        self.assertEqual(r.status_code, 201, r.json)
        return r.json

    def execute(self, o, client=None, headers=None):
        return (client or self.client).post('/api/workspace/orders/'+o['id']+'/execute', json={'version': o['version']}, headers=headers or self.headers)

    def test_reads_require_auth_mutations_require_csrf_and_roles(self):
        guest = self.app.test_client()
        self.assertEqual(guest.get('/api/workspace').status_code, 401)
        self.assertEqual(guest.get('/workspace').status_code, 302)
        self.assertEqual(self.client.post('/api/workspace/orders', json=self.body()).status_code, 403)
        o = self.create()
        for role in ['viewer', 'coordinator']:
            self.app.auth.add('prep-'+role, 'Preparation-test-7391', role)
            c = self.app.test_client();t = c.get('/api/session').json['csrf']
            c.post('/api/auth/login', json={'username': 'prep-'+role, 'password': 'Preparation-test-7391'}, headers={'X-CSRF-Token': t})
            h = {'X-CSRF-Token': c.get('/api/session').json['csrf']}
            self.assertEqual(self.execute(o, c, h).status_code, 403)
            self.assertEqual(c.post('/api/workspace/orders', json=self.body(), headers=h).status_code, 403 if role=='viewer' else 201)

    def test_immutable_brief_actual_reconstruction_and_review_are_separate(self):
        o = self.create();self.assertEqual(o['status'], 'draft')
        before = json.dumps(o['spec'], sort_keys=True)
        response = self.execute(o);self.assertEqual(response.status_code, 202, response.json)
        item = response.json
        self.assertEqual(item['status'], 'pending_review')
        self.assertEqual(item['analysis']['foreground_voxels'], 75846)
        self.assertIsNone(item['analysis']['candidate_count'])
        self.assertFalse(item['analysis']['model_inference_executed'])
        self.assertEqual(json.dumps(item['spec'], sort_keys=True), before)
        self.assertGreater(item['machine_elapsed_ms'], 0)
        self.assertFalse(item['device_execution_verified'])
        self.assertEqual(self.execute(o).status_code, 400)
        self.assertEqual(self.app.store.tasks(o['id']), [])
        a = item['analysis']
        r = self.client.post('/api/analyses/'+a['id']+'/review', json={'version': a['version'], 'decision': 'approve', 'review_confirmed': True}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.json)
        board = self.client.get('/api/workspace').json
        self.assertEqual(board['summary']['reviewed'], 1)
        export = self.client.get('/api/workspace/orders/'+o['id']+'/export').json
        self.assertEqual(export['analysis']['analysis']['id'], a['id'])
        self.assertTrue(export['audit']['valid'])

    def test_unknown_acute_missing_history_and_missing_consent_block_execution(self):
        for context in [{'acute': None, 'history_complete': True, 'consent': True},
                        {'acute': True, 'history_complete': True, 'consent': True},
                        {'acute': False, 'history_complete': False, 'consent': True},
                        {'acute': False, 'history_complete': True, 'consent': None}]:
            o = self.create(context=context)
            self.assertTrue(o['blockers'])
            with patch.object(self.app.analysis, 'execute') as run:
                self.assertEqual(self.execute(o).status_code, 400)
                run.assert_not_called()

    def test_changed_pixels_and_changed_spec_do_not_execute(self):
        o = self.create();case = self.app.store.case(o['case_id'])
        file = Path(self.temp.name)/case['slices'][0];original = file.read_bytes()
        file.write_bytes(original+b'changed')
        with patch.object(self.app.analysis, 'execute') as run:
            self.assertEqual(self.execute(o).status_code, 400);run.assert_not_called()
        file.write_bytes(original)
        altered = self.app.preparation.get(o['id']);altered['spec']['settings']['threshold'] = 23
        self.app.preparation.save(altered, 'test_spec_tamper')
        self.assertEqual(self.execute(o).status_code, 400)

    def test_adjustment_creates_a_new_snapshot_without_overwriting_prior(self):
        first = self.create()
        second = self.create(goal='只檢查另一種參考類別', settings={'reference_class': 2})
        self.assertNotEqual(first['id'], second['id'])
        self.assertNotEqual(first['spec_sha256'], second['spec_sha256'])
        self.assertEqual(self.app.preparation.get(first['id'])['spec']['settings']['reference_class'], 0)

    def test_missing_real_model_remains_blocked_not_synthetic_prediction(self):
        o = self.create(case_id='demo-ct-positive', settings={'mode': 'model', 'segmentation_mode': 'threshold'})
        r = self.execute(o);self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json['status'], 'needs_attention')
        self.assertIsNone(r.json['analysis']['candidate_count'])
        self.assertTrue(any(s['status']=='blocked' for s in r.json['analysis']['stages']))

    def test_invalid_and_untrusted_settings_are_rejected(self):
        for changes in [{'settings': {'mode':'demo'}}, {'settings': {'center':True}},
                        {'settings': {'threshold':float('nan')}}, {'settings': {'actor':'forged'}},
                        {'context': {'acute':0,'consent':True,'history_complete':True}},
                        {'settings': {'use_llm':True,'model':'not-ready'}}]:
            r = self.client.post('/api/workspace/orders', json=self.body(**changes), headers=self.headers)
            self.assertEqual(r.status_code, 400, r.json)

    def test_restart_recovers_queued_job_without_reexecuting(self):
        o = self.create();o.update(status='queued');self.app.preparation.save(o, 'test_queued')
        second = create_app(Path(self.temp.name), synchronous=True)
        try:
            self.assertEqual(second.preparation.get(o['id'])['status'], 'blocked')
            self.assertIsNone(second.preparation.get(o['id'])['analysis_id'])
        finally:second.pool.shutdown(wait=True)

    def test_local_async_jobs_return_queued_and_complete_on_the_existing_worker(self):
        app = create_app(Path(self.temp.name)/'async', synchronous=False, access_control=False)
        started, release = threading.Event(), threading.Event()
        original = app.analysis.execute
        def controlled(body, actor):
            started.set();release.wait(3)
            return original(body, actor)
        try:
            app.analysis.execute = controlled
            o = app.preparation.create(self.body(), 'async-reader')
            item = app.preparation.submit(o['id'], 1, 'async-reader')
            self.assertIn(item['status'], {'queued','running'})
            self.assertTrue(started.wait(2))
            self.assertIsNone(app.preparation.get(o['id'])['analysis_id'])
            release.set();app.pool.shutdown(wait=True)
            final = app.preparation.present(app.preparation.get(o['id']))
            self.assertEqual(final['status'], 'pending_review')
            self.assertEqual(final['analysis']['foreground_voxels'], 75846)
        finally:release.set();app.pool.shutdown(wait=True)

    def test_reviewed_job_returns_to_attention_if_source_changes(self):
        item = self.execute(self.create()).json;a=item['analysis']
        r=self.client.post('/api/analyses/'+a['id']+'/review',json={'version':a['version'],'decision':'approve','review_confirmed':True},headers=self.headers)
        self.assertEqual(r.status_code,200,r.json)
        case=self.app.store.case(item['case_id']);file=Path(self.temp.name)/case['slices'][0]
        file.write_bytes(file.read_bytes()+b'changed')
        board=self.client.get('/api/workspace').json
        self.assertEqual(board['summary']['reviewed'],0)
        self.assertEqual(board['orders'][0]['status'],'needs_attention')

    def test_cost_scenario_is_explicit_decimal_math_and_persists(self):
        data = {'cases_per_month':100,'manual_minutes':20,'assisted_minutes':10,'hourly_ntd':600,'monthly_cost_ntd':5000}
        r = self.client.post('/api/workspace/cost-scenario', json=data, headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['gross_capacity_value_ntd'], 10000)
        self.assertEqual(r.json['net_scenario_value_ntd'], 5000)
        self.assertEqual(r.json['break_even_cases'], 50)
        self.assertFalse(r.json['clinical_effect_verified'])
        self.assertEqual(self.client.get('/api/workspace').json['cost_scenario']['inputs'], data)
        data['assisted_minutes'] = 30
        result = cost_scenario(data)
        self.assertLess(result['net_scenario_value_ntd'], 0)
        self.assertIsNone(result['break_even_cases'])
        for bad in [True, float('inf'), -1]:
            with self.assertRaises(ValueError):cost_scenario(data | {'hourly_ntd': bad})


class ModelGateTests(unittest.TestCase):
    def test_concurrent_model_clients_are_serialized_and_failure_releases_gate(self):
        gate=ModelGate();started=threading.Event();release=threading.Event();second=threading.Event()
        def first():
            with gate.slot('/chat/completions'):
                started.set();release.wait(3)
        def other():
            with gate.slot('/audio/transcriptions'):second.set()
        a=threading.Thread(target=first);b=threading.Thread(target=other)
        a.start();self.assertTrue(started.wait(2));b.start()
        try:self.assertFalse(second.wait(.05))
        finally:release.set();a.join(3);b.join(3)
        self.assertTrue(second.is_set());self.assertEqual(gate.snapshot()['completed'],2)
        with self.assertRaises(RuntimeError):
            with gate.slot('/chat/completions'):raise RuntimeError('model failed')
        self.assertIsNone(gate.snapshot()['active']);self.assertEqual(gate.snapshot()['failed'],1)
        self.assertFalse(gate.snapshot()['device_execution_verified'])

    def test_wait_timeout_does_not_poison_later_requests(self):
        gate=ModelGate();held=threading.Event();release=threading.Event()
        def holding():
            with gate.slot('/chat/completions'):held.set();release.wait(3)
        t=threading.Thread(target=holding);t.start();self.assertTrue(held.wait(2))
        try:
            from werkzeug.exceptions import ServiceUnavailable
            with self.assertRaises(ServiceUnavailable):
                with gate.slot('/chat/completions', timeout=.02):pass
            self.assertEqual(gate.snapshot()['waiting'],0)
        finally:release.set();t.join(3)
        with gate.slot('/chat/completions'):pass
        self.assertEqual(gate.snapshot()['completed'],2)


if __name__=='__main__':unittest.main()
