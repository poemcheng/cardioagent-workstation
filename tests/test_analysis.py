import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np

from cardio.analysis import Analysis, box_iou, compare_boxes, compare_masks
from cardio.planner import Planner
from cardio.seed import seed
from cardio.store import Store


ROOT = Path(__file__).resolve().parents[1]


class MetricTests(unittest.TestCase):
    def test_iou_matching_is_one_to_one_and_counts_false_positives(self):
        result = compare_boxes([{"xyxy": [0, 0, 10, 10]}, {"xyxy": [0, 0, 10, 10]},
                                {"xyxy": [30, 30, 40, 40]}], [[0, 0, 10, 10], [20, 20, 25, 25]])
        self.assertEqual((result["tp"], result["fp"], result["fn"]), (1, 2, 1))
        self.assertAlmostEqual(result["precision"], 1 / 3)
        self.assertAlmostEqual(result["recall"], .5)
        self.assertAlmostEqual(result["f1"], .4)
        self.assertEqual(result["mean_iou"], 1)
        self.assertEqual(box_iou([0, 0, 10, 10], [5, 0, 15, 10]), 1 / 3)
        self.assertEqual(len(result["matches"]), 1)

    def test_empty_detection_metrics_remain_undefined(self):
        result = compare_boxes([], [])
        self.assertIsNone(result["precision"])
        self.assertIsNone(result["recall"])
        self.assertIsNone(result["f1"])

    def test_mask_metrics_include_nonperfect_overlap_and_explicit_empty_convention(self):
        truth = np.zeros((2, 3, 4), bool)
        truth[0, 0, :2] = True
        pred = np.zeros_like(truth)
        pred[0, 0, 1:3] = True
        result = compare_masks(pred, truth)
        self.assertEqual(result["dice"], .5)
        self.assertEqual(result["iou"], 1 / 3)
        empty = compare_masks(np.zeros_like(truth), np.zeros_like(truth))
        self.assertTrue(empty["both_empty"])
        self.assertEqual(empty["dice"], 1.)
        with self.assertRaises(ValueError):
            compare_masks(pred, np.full_like(truth, 2, dtype=int))


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.store = Store(self.data / "test.sqlite3")
        seed(self.store, self.data, ROOT)
        self.planner = Planner({"planner_model": ""})
        self.detector = Mock()
        self.detector.detect.side_effect = ValueError("未配置 CT 模型")
        self.analysis = Analysis(ROOT, self.data, self.store, self.detector, self.planner)

    def tearDown(self):
        self.temp.cleanup()

    def execute(self, **kwargs):
        return self.analysis.execute({"case_id": "demo-ct-positive", "mode": "demo", "center": 1} | kwargs,
                                     "test-researcher")

    def test_loaded_slices_are_independent_snapshots_without_live_file_locks(self):
        case = self.store.case('demo-ct-positive')
        arrays, hashes = self.analysis._load(case)
        self.assertTrue(all(not isinstance(a, np.memmap) for a in arrays))
        first = arrays[0].copy()
        path = self.data / case['slices'][0]
        raw = path.read_bytes()
        # A retained mapped input would prevent this operation on Windows.
        path.unlink()
        np.save(path, np.zeros_like(first), allow_pickle=False)
        np.testing.assert_array_equal(arrays[0], first)
        path.write_bytes(raw)
        self.assertEqual(len(hashes), len(case['slices']))

    def test_actual_synthetic_pipeline_measures_sources_and_persists_for_review(self):
        result = self.execute()
        self.assertEqual(result["status"], "pending_review")
        self.assertEqual(result["source"]["shape_zyx"], [3, 512, 512])
        self.assertEqual(len(result["source"]["slice_sha256"]), 3)
        self.assertEqual(len(result["recognition"]["candidates"]), 2)
        self.assertFalse(result["recognition"]["inference_executed"])
        self.assertEqual(result["comparison"]["detection"]["precision"], 1.)
        self.assertEqual(result["comparison"]["detection"]["scope"], "synthetic_fixture_only_not_clinical_accuracy")
        self.assertIsNotNone(result["reconstruction"], result["stages"])
        self.assertFalse(result["explanation"]["saliency_available"])
        self.assertEqual(result["graph"]["kind"], "provenance_graph_not_causal_medical_graph")
        again = Analysis(ROOT, self.data, self.store, self.detector, self.planner)
        self.assertEqual(again.get(result["id"]), result)
        self.assertEqual(again.list(result["case_id"])[0]["id"], result["id"])
        self.assertTrue(self.store.verify()["valid"])
        self.assertTrue((self.data / "analyses" / (result["id"] + ".json")).is_file())

    def test_missing_trained_model_is_disclosed_without_synthetic_substitution(self):
        result = self.execute(mode="model")
        self.assertIsNone(result["recognition"])
        self.assertEqual(next(s for s in result["stages"] if s["key"] == "recognition")["status"], "blocked")
        self.assertFalse(result["comparison"]["detection"]["available"])
        self.assertIsNotNone(result["reconstruction"])
        self.assertIn("沒有可用", result["explanation"]["summary"])

    def test_mri_inspection_has_no_clinical_accuracy_and_never_calls_llm(self):
        self.planner.local_model = Mock(side_effect=AssertionError("research image must not reach model"))
        result = self.execute(case_id="research-mri-01", mode="inspect", use_llm=True, model="test-model")
        self.assertEqual(result["source"]["shape_zyx"], [8, 512, 428])
        self.assertEqual(result["recognition"]["scope"], "image_inspection_only")
        self.assertFalse(result["comparison"]["detection"]["available"])
        self.assertTrue(result["comparison"]["segmentation"]["available"])
        self.assertEqual(result["comparison"]["segmentation"]["scope"], "provided_research_label_agreement_not_clinical_accuracy")
        self.assertEqual(result["reconstruction"]["mesh"]["units"], "voxel")
        self.assertIsNone(result["reconstruction"]["statistics"]["physical_volume_mm3"])
        self.assertEqual(result["language"]["source"], "rules_fallback")
        self.planner.local_model.assert_not_called()
        with self.assertRaises(ValueError):
            self.execute(case_id="research-mri-01", mode="demo")

    def test_llm_only_receives_synthetic_workflow_flags_and_rejects_unknown_sources(self):
        self.planner.local_model = Mock(return_value={"id": "test-model", "recipe": "flm"})
        self.planner.request = Mock(return_value={"id": "test-reply", "choices": [{"message": {
            "content": json.dumps({"summary": "合成軟體分析待人工覆核。", "source_ids": ["quality"]})}}]})
        result = self.execute(use_llm=True, model="test-model")
        self.assertEqual(result["language"]["source"], "lemonade")
        facts = json.loads(self.planner.request.call_args.args[1]["messages"][1]["content"])
        self.assertEqual(set(facts), {"synthetic", "source_ids", "candidate_count", "inference_executed",
                                      "reconstruction_available", "geometry_unit", "stages"})
        self.assertNotIn("case_id", facts)
        self.assertNotIn("image", facts)
        self.assertNotIn("age", facts)
        self.planner.request.return_value["choices"][0]["message"]["content"] = json.dumps({"summary": "text", "source_ids": ["unknown"]})
        self.assertEqual(self.execute(use_llm=True, model="test-model")["language"]["source"], "rules_fallback")

    def test_comparison_requires_same_case_source_and_center(self):
        prior = self.execute()
        same = self.execute(compare_to=prior["id"])
        self.assertTrue(same["comparison"]["prior"]["available"])
        self.assertTrue(same["comparison"]["prior"]["same_settings"])
        self.assertEqual(same["comparison"]["prior"]["candidate_count_delta"], 0)
        other = self.execute(case_id="demo-ct-zero", compare_to=prior["id"])
        self.assertFalse(other["comparison"]["prior"]["available"])
        center = self.execute(center=0, compare_to=prior["id"])
        self.assertFalse(center["comparison"]["prior"]["available"])
        changed = self.execute(threshold=200, compare_to=prior["id"])
        self.assertFalse(changed["comparison"]["prior"]["same_settings"])

    def test_discussion_and_review_use_versions_and_never_create_tasks(self):
        result = self.execute()
        discussed = self.analysis.attach_discussion(result["id"], {"status": "incomplete", "procedure": None},
                                                     "test-clinician", result["version"])
        self.assertEqual(discussed["version"], 2)
        with self.assertRaises(ValueError):
            self.analysis.review(result["id"], {"version": 1, "decision": "approve"}, "test-clinician")
        approved = self.analysis.review(result["id"], {"version": 2, "decision": "approve", "review_confirmed": True, "reason": "核對研究證據"}, "test-clinician")
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["review"]["actor"], "test-clinician")
        self.assertEqual(approved["version"], 3)
        self.assertEqual(self.store.tasks(result["id"]), [])
        self.assertIsNone(approved["discussion"]["procedure"])
        with self.assertRaises(ValueError):
            self.analysis.attach_discussion(result["id"], {"status": "changed"}, "test-clinician", 3)
        with self.assertRaises(ValueError):
            self.analysis.review(result["id"], {"version": 3, "decision": "approve"}, "test-clinician")
        exported = self.analysis.export(result["id"])
        self.assertEqual(exported["analysis"]["status"], "approved")
        self.assertTrue(exported["audit"]["valid"])
        self.assertEqual([r["version"] for r in exported["revisions"]], [1, 2])

    def test_persisted_mask_and_source_hashes_prevent_incorrect_overlays(self):
        result = self.execute()
        mask = self.analysis.mask(result["id"])
        self.assertEqual(mask.dtype, np.bool_)
        self.assertEqual(mask.shape, (3, 512, 512))
        self.assertEqual(int(mask.sum()), result["segmentation"]["foreground_voxels"])
        path = self.data / "analyses" / (result["id"] + ".mask.npy")
        original = path.read_bytes()
        np.save(path, ~mask, allow_pickle=False)
        with self.assertRaisesRegex(ValueError, "雜湊不符"):
            self.analysis.mask(result["id"])
        path.write_bytes(original)
        source = self.data / self.store.case(result["case_id"])["slices"][0]
        pixels = np.load(source, allow_pickle=False)
        pixels[0, 0] += 1
        np.save(source, pixels, allow_pickle=False)
        with self.assertRaisesRegex(ValueError, "影像與分析來源雜湊不同"):
            self.analysis.mask(result["id"])

    def test_registered_segmentation_mask_drives_all_metrics_mesh_and_overlay(self):
        from cardio.volume import generate_phantom
        case = generate_phantom(self.data)
        self.store.put_case(case)
        truth = np.load(self.data / case["reference_mask"], allow_pickle=False)
        learned = truth.copy()
        learned[:16] = False
        self.analysis.segmenter = Mock()
        self.analysis.segmenter.segment.return_value = {"mask": learned,
            "metadata": {"method": "registered_onnx", "model": "test-model", "sha256": "test-hash",
                         "class_label": "declared test class", "accuracy_verified": False}}
        result = self.execute(case_id=case["id"], mode="inspect", center=16, threshold=999,
                              segmentation_mode="registered_model")
        self.assertEqual(result["segmentation"]["method"], "registered_onnx")
        self.assertEqual(result["reconstruction"]["segmentation"]["method"], "registered_onnx")
        self.assertEqual(result["reconstruction"]["statistics"]["foreground_voxels"], int(learned.sum()))
        self.assertGreater(result["comparison"]["segmentation"]["dice"], 0)
        self.assertLess(result["comparison"]["segmentation"]["dice"], 1)
        np.testing.assert_array_equal(self.analysis.mask(result["id"]), learned)
        self.analysis.segmenter.segment.assert_called_once()

    def test_unavailable_registered_segmentation_does_not_fall_back_to_threshold(self):
        self.analysis.segmenter = Mock()
        self.analysis.segmenter.segment.side_effect = ValueError("未配置分割模型")
        result = self.execute(segmentation_mode="registered_model")
        self.assertIsNone(result["segmentation"])
        self.assertIsNone(result["reconstruction"])
        self.assertFalse(result["comparison"]["segmentation"]["available"])
        self.assertFalse((self.data / "analyses" / (result["id"] + ".mask.npy")).exists())
        with self.assertRaises(ValueError):
            self.analysis.mask(result["id"])

    def test_blocked_and_inspection_results_are_not_counted_as_zero_detection(self):
        failed = self.execute(mode="model")
        valid = self.execute(compare_to=failed["id"])
        self.assertFalse(valid["comparison"]["prior"]["available"])
        self.assertIsNone(valid["comparison"]["prior"]["candidate_count_delta"])
        blocked = self.execute(mode="model", compare_to=valid["id"])
        self.assertFalse(blocked["comparison"]["prior"]["available"])
        inspected = self.execute(mode="inspect")
        compared = self.execute(compare_to=inspected["id"])
        self.assertFalse(compared["comparison"]["prior"]["available"])
        self.assertIsNone(compared["comparison"]["prior"]["candidate_count_delta"])

    def test_provided_research_labels_reconstruct_without_self_scoring(self):
        result = self.execute(case_id="research-mri-01", mode="inspect", center=4,
                              segmentation_mode="provided_reference", reference_class=0, threshold=999)
        self.assertEqual(result["segmentation"]["method"], "provided_research_labels", result["stages"])
        self.assertFalse(result["segmentation"]["inference_executed"])
        self.assertEqual(result["segmentation"]["foreground_voxels"], 75846)
        self.assertFalse(result["comparison"]["segmentation"]["available"])
        self.assertIsNone(result["comparison"]["segmentation"]["dice"])
        self.assertIn("同一標籤", result["comparison"]["segmentation"]["reason"])
        self.assertIsNotNone(result["reconstruction"])
        self.assertEqual(int(self.analysis.mask(result["id"]).sum()), 75846)
        self.assertIsNone(result["reconstruction"]["statistics"]["physical_volume_mm3"])
        selected = self.execute(case_id="research-mri-01", mode="inspect", center=4,
                                segmentation_mode="provided_reference", reference_class=2)
        self.assertEqual(selected["segmentation"]["foreground_voxels"], 25573)
        self.assertNotEqual(selected["segmentation"]["mask_sha256"], result["segmentation"]["mask_sha256"])

    def test_independent_mri_threshold_comparison_does_not_use_labels_for_prediction(self):
        result = self.execute(case_id="research-mri-01", mode="inspect", center=4, threshold=128)
        score = result["comparison"]["segmentation"]
        self.assertTrue(score["available"], result["stages"])
        self.assertEqual(score["scope"], "provided_research_label_agreement_not_clinical_accuracy")
        self.assertGreater(score["dice"], 0)
        self.assertLess(score["dice"], 1)
        arrays = [np.load(self.data / p, allow_pickle=False) for p in self.store.case("research-mri-01")["slices"]]
        np.testing.assert_array_equal(self.analysis.mask(result["id"]), np.stack(arrays) >= 128)
        self.assertEqual(score["reference_voxels"], 75846)

    def test_nested_automatic_procedure_is_rejected(self):
        result = self.execute()
        with self.assertRaises(ValueError):
            self.analysis.attach_discussion(result["id"], {"recommendation": {"procedure": "CABG"}}, "test-clinician", 1)
        self.assertEqual(self.analysis.get(result["id"])["version"], 1)

    def test_invalid_parameters_and_path_traversal_are_rejected(self):
        for changed in ({"center": True}, {"center": -1}, {"center": 3}, {"threshold": float("nan")},
                        {"threshold": True}, {"use_llm": "yes"}, {"run_id": ["invalid"]},
                        {"compare_to": "../secrets"}, {"mode": "surgeon"}, {"segmentation_mode": "madeup"},
                        {"reference_class": True}, {"reference_class": -1}, {"reference_class": 4}, {"reference_class": "1"}):
            with self.subTest(changed=changed):
                with self.assertRaises((ValueError, KeyError)):
                    self.execute(**changed)
        with self.assertRaises(ValueError):
            self.analysis.get("../file")

    def test_linked_acute_flag_skips_all_pixel_and_model_work(self):
        case = self.store.case("demo-ct-positive")
        ident = "acute-test"
        self.store.save_run({"id": ident, "case_id": case["id"], "status": "emergency", "version": 1,
                             "context": {"acute": True}}, "test_acute")
        self.analysis._load = Mock(side_effect=AssertionError("no image I/O"))
        self.planner.local_model = Mock(side_effect=AssertionError("no model"))
        result = self.execute(run_id=ident, use_llm=True, model="test-model")
        self.assertTrue(result["run_snapshot"]["acute"])
        self.assertTrue(all(s["status"] == "blocked" for s in result["stages"]))
        self.analysis._load.assert_not_called()
        self.detector.detect.assert_not_called()

    def test_corrupt_or_mismatched_sources_block_downstream_without_false_metrics(self):
        c = self.store.case("demo-ct-positive")
        self.store.save_run({"id": "source-run", "case_id": c["id"], "status": "pending_review", "version": 1,
                             "context": {"acute": False}, "snapshot": {"slice_sha256": ["wrong"]}}, "test_source")
        result = self.execute(run_id="source-run")
        self.assertIsNone(result["quality"])
        self.assertIsNone(result["recognition"])
        self.assertIsNone(result["comparison"])
        self.assertEqual(next(s for s in result["stages"] if s["key"] == "quality")["status"], "blocked")
        self.assertIsNone(result["reconstruction"])

    def test_independent_synthetic_mask_is_only_used_for_evaluation(self):
        from cardio.volume import generate_phantom
        case = generate_phantom(self.data)
        self.store.put_case(case)
        result = self.execute(case_id=case["id"], mode="inspect", center=16, threshold=120)
        score = result["comparison"]["segmentation"]
        self.assertTrue(score["available"], result["stages"])
        self.assertEqual(score["dice"], 1.)
        self.assertEqual(score["scope"], "synthetic_fixture_only_not_clinical_accuracy")
        self.assertFalse(result["reconstruction"]["segmentation"]["trained"])
        volume = result["reconstruction"]["statistics"]["physical_volume_mm3"]
        self.assertGreater(volume, 0)
        # Changing known reference truth blocks evaluation, not segmentation or mesh.
        truth = self.data / case["reference_mask"]
        np.save(truth, np.zeros_like(np.load(truth, allow_pickle=False)))
        changed = self.execute(case_id=case["id"], mode="inspect", center=16, threshold=120)
        self.assertIsNone(changed["comparison"])
        self.assertEqual(next(s for s in changed["stages"] if s["key"] == "comparison")["status"], "blocked")
        self.assertEqual(changed["reconstruction"]["statistics"]["physical_volume_mm3"], volume)
        self.assertEqual(changed["reconstruction"]["mesh"], result["reconstruction"]["mesh"])


if __name__ == "__main__":
    unittest.main()
