"""Real ORT inference over purpose-built graphs, not clinical segmentation tests."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

from cardio.imaging import sha256
from cardio.segmentation import Segmenter, validate_manifest, validate_model
from scripts.register_segmentation import register


def graph(path, bad_output=False, input_channels=1, nonfinite=False):
    h, w = 4, 5
    inp = helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, input_channels, h, w])
    shape = [1, 3 if bad_output else 2, h, w]
    out = helper.make_tensor_value_info("y", TensorProto.FLOAT, shape)
    if nonfinite or bad_output or input_channels != 1:
        array = np.full(shape, np.nan if nonfinite else 0, np.float32)
        nodes = [helper.make_node("Constant", [], ["y"], value=numpy_helper.from_array(array))]
    else:
        nodes = [helper.make_node("Constant", [], ["one"], value=numpy_helper.from_array(np.array(1, np.float32))),
                 helper.make_node("Sub", ["one", "x"], ["background"]),
                 helper.make_node("Concat", ["background", "x"], ["y"], axis=1)]
    model = helper.make_model(helper.make_graph(nodes, "segmentation-contract-test", [inp], [out]),
                              opset_imports=[helper.make_opsetid("", 17)], ir_version=8)
    onnx.save(model, str(path))


class SegmentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "models").mkdir()
        self.model = self.root / "models" / "fixture.onnx"
        graph(self.model)
        self.manifest = {"file": self.model.name, "name": "Purpose-built test graph; not clinical",
                         "sha256": sha256(self.model), "trained": True,
                         "provenance": "Unit-test declaration only; no patient training or clinical validity",
                         "modality": "MRI", "input_size": [4, 5], "target_class": 1,
                         "class_labels": ["background", "test_foreground"], "window": [0, 255],
                         "output": "logits-nchw"}
        self.save()
        self.arrays = [np.arange(20, dtype=np.float32).reshape(4, 5) * 13,
                       np.full((4, 5), 255, np.float32)]
        self.case = {"modality": "MRI", "synthetic": True, "is_hu": False}

    def tearDown(self):
        self.temp.cleanup()

    def save(self):
        (self.root / "models/segmentation.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_real_ort_multiclass_argmax_preserves_full_volume(self):
        segmenter = Segmenter(self.root, {})
        result = segmenter.segment(self.arrays, self.case)
        self.assertEqual(result["mask"].shape, (2, 4, 5))
        self.assertEqual(result["mask"].dtype, np.bool_)
        np.testing.assert_array_equal(result["mask"], np.asarray(self.arrays) > 127.5)
        self.assertEqual(result["metadata"]["method"], "registered_onnx")
        self.assertEqual(result["metadata"]["anatomical_label"], "test_foreground")
        self.assertFalse(result["metadata"]["accuracy_verified"])
        self.assertFalse(result["metadata"]["anatomy_verified"])
        self.assertFalse(result["metadata"]["preprocessing"]["resizing"])
        self.assertIn("CPUExecutionProvider", result["metadata"]["provider"])
        session = segmenter.session
        segmenter.segment(self.arrays, self.case)
        self.assertIs(segmenter.session, session)

    def test_explicit_hu_window_normalization_is_used(self):
        self.manifest["modality"] = "CT"
        self.manifest["window"] = [-1000, 1000]
        self.save()
        images = [np.linspace(-2000, 2000, 20, dtype=np.float32).reshape(4, 5)]
        result = Segmenter(self.root, {}).segment(images, {"modality": "CT", "is_hu": True})
        np.testing.assert_array_equal(result["mask"], np.asarray(images) > 0)
        self.assertEqual(result["metadata"]["preprocessing"]["window"], [-1000., 1000.])

    def test_missing_model_has_no_threshold_fallback(self):
        (self.root / "models/segmentation.json").unlink()
        with self.assertRaisesRegex(ValueError, "未改用閾值"):
            Segmenter(self.root, {}).segment(self.arrays, self.case)

    def test_hash_and_model_path_are_enforced(self):
        self.model.write_bytes(self.model.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            Segmenter(self.root, {}).segment(self.arrays, self.case)
        self.manifest["file"] = "../fixture.onnx"
        self.save()
        with self.assertRaisesRegex(ValueError, "檔名"):
            Segmenter(self.root, {}).manifest()

    def test_modality_dimensions_and_export_range_fail_closed(self):
        for arrays, case in ((self.arrays, {"modality": "CT"}),
                             ([np.zeros((5, 4), np.float32)], self.case),
                             ([np.full((4, 5), -1, np.float32)], self.case),
                             ([np.full((4, 5), 256, np.float32)], self.case),
                             (self.arrays, self.case | {"is_hu": 1})):
            with self.subTest(case=case, shape=np.asarray(arrays).shape), self.assertRaises(ValueError):
                Segmenter(self.root, {}).segment(arrays, case)

    def test_invalid_array_shapes_and_nonfinite_values_fail(self):
        for arrays in ([], np.array(3), [np.zeros((4, 5)), np.zeros((4, 4))],
                       [np.full((4, 5), np.nan)], [np.full((4, 5), np.inf)]):
            with self.subTest(kind=type(arrays).__name__), self.assertRaises(ValueError):
                Segmenter(self.root, {}).segment(arrays, self.case)

    def test_manifest_requires_trained_provenance_explicit_window_and_class_contract(self):
        for update in ({"trained": False}, {"trained": 1}, {"provenance": ""}, {"window": [0, 0]},
                       {"window": [0, float("nan")]}, {"window": [False, 255]}, {"target_class": True},
                       {"target_class": 2}, {"class_labels": ["a", "a"]}, {"output": "probability"},
                       {"input_size": [4.0, 5]}, {"sha256": "bad"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_manifest(self.manifest | update)

    def test_actual_output_channel_shape_input_and_finite_contract(self):
        for kwargs in ({"bad_output": True}, {"input_channels": 3}, {"nonfinite": True}):
            graph(self.model, **kwargs)
            self.manifest["sha256"] = sha256(self.model)
            self.save()
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Segmenter(self.root, {}).segment(self.arrays, self.case)

    def test_unavailable_provider_is_not_silently_replaced(self):
        with self.assertRaisesRegex(ValueError, "執行器不可用"):
            Segmenter(self.root, {"segmentation_provider": "NotARealProvider"}).segment(self.arrays, self.case)

    def test_registration_validates_and_writes_hash_addressed_model(self):
        out = register(self.model, self.root, self.manifest)
        self.assertEqual(out["file"], "segmentation-" + self.manifest["sha256"] + ".onnx")
        self.assertTrue((self.root / "models" / out["file"]).is_file())
        actual = Segmenter(self.root, {}).segment(self.arrays, self.case)
        np.testing.assert_array_equal(actual["mask"], np.asarray(self.arrays) > 127.5)

    def test_failed_registration_preserves_previous_manifest(self):
        before = (self.root / "models/segmentation.json").read_bytes()
        with self.assertRaises(ValueError):
            register(self.model, self.root, self.manifest | {"sha256": "0" * 64})
        self.assertEqual((self.root / "models/segmentation.json").read_bytes(), before)
        graph(self.model, nonfinite=True)
        with self.assertRaises(ValueError):
            register(self.model, self.root, self.manifest | {"sha256": sha256(self.model)})
        self.assertEqual((self.root / "models/segmentation.json").read_bytes(), before)
        self.assertEqual(list((self.root / "models").glob(".segmentation-*")), [])


if __name__ == "__main__":
    unittest.main()
