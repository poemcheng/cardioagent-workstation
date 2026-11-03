"""Acceptance gates must distinguish real model work from data readiness/bypass."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import accept_aipc, aipc_preflight
from scripts.evaluate_aipc import evaluate


class AcceptanceGateTests(unittest.TestCase):
    def test_strict_preflight_fails_without_model_while_baseline_remains_usable(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(aipc_preflight,'check',return_value={'baseline_ready':True,'local_llm_ready':False}):
            path=str(Path(temp)/'probe.json')
            self.assertEqual(aipc_preflight.main(['--output',path]),0)
            self.assertEqual(aipc_preflight.main(['--require-llm','--output',path]),1)

    def test_only_urgent_rules_or_empty_outputs_cannot_pass_llm_acceptance(self):
        urgent={'summary':{'attempted':1,'failed_outputs':0,'accepted_outputs':1,'exact_full_turn_validation_passed':1},'local_model_outputs':0}
        self.assertFalse(accept_aipc.llm_passed(urgent))
        self.assertFalse(accept_aipc.llm_passed({'summary':{},'local_model_outputs':0}))
        real=urgent|{'local_model_outputs':1}
        self.assertTrue(accept_aipc.llm_passed(real))
        self.assertFalse(accept_aipc.llm_passed(real|{'summary':real['summary']|{'failed_outputs':1}}))

    def test_required_llm_unavailable_preserves_core_results_but_fails_requested_scope(self):
        baseline={'summary':{'attempted':412,'failed_outputs':0},'actual_engine_counts':{'lexical_section_baseline':411,'explicit_acute_flag_rules':1}}
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(accept_aipc,'check',return_value={'baseline_ready':True,'local_llm_ready':False}), \
             patch.object(accept_aipc,'inspect',return_value={'workflow_demo_ready':True}), \
             patch.object(accept_aipc,'evaluate',return_value=baseline) as evaluator, \
             patch.object(accept_aipc,'image_flow',return_value={'passed':True,'cases':[]}):
            path=Path(temp)/'new-run'
            report=accept_aipc.run(path,use_llm=True,model='unavailable-model')
            self.assertFalse(report['passed_requested_scope'])
            self.assertTrue(report['checks']['baseline']['passed'])
            self.assertFalse(report['checks']['llm']['passed'])
            self.assertEqual(evaluator.call_count,1)
            self.assertFalse(report['npu_execution_verified'])
            self.assertEqual(json.loads((path/'acceptance.json').read_text())['selected_model'],'unavailable-model')
            with self.assertRaises(FileExistsError):accept_aipc.run(path)

    def test_empty_split_is_not_a_successful_evaluation(self):
        with self.assertRaisesRegex(ValueError,'No cases'):evaluate(split='not-a-split')
