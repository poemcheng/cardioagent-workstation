"""Checks actual supplied label assets without treating them as predictions."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import numpy as np

from cardio.reference_labels import load_reference_mask, prepare_reference_labels


ROOT = Path(__file__).resolve().parents[1]


class ReferenceLabelsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        slices = []
        for source in sorted((ROOT / "assets/mri").glob("[0-9][0-9][0-9][0-9].npy")):
            path = self.data / "images" / source.name
            path.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, path)
            slices.append(str(path.relative_to(self.data)))
        self.case = {"id": "research-mri-01", "modality": "MRI", "synthetic": False, "slices": slices}
        self.case.update(prepare_reference_labels(self.data, ROOT))

    def tearDown(self):
        self.temp.cleanup()

    def test_actual_reference_counts_and_union_are_preserved(self):
        counts = {1: 26474, 2: 25573, 3: 23799, 0: 75846}
        for class_value, expected in counts.items():
            with self.subTest(class_value=class_value):
                result = load_reference_mask(self.data, self.case, class_value)
                self.assertEqual(result["mask"].shape, (8, 512, 428))
                self.assertEqual(result["mask"].dtype, np.bool_)
                self.assertEqual(int(result["mask"].sum()), expected)
                self.assertEqual(result["metadata"]["selected_voxels"], expected)
        union = load_reference_mask(self.data, self.case)["mask"]
        classes = [load_reference_mask(self.data, self.case, value)["mask"] for value in (1, 2, 3)]
        np.testing.assert_array_equal(union, np.logical_or.reduce(classes))

    def test_source_and_unknown_mapping_are_explicit(self):
        result = load_reference_mask(self.data, self.case, 1)
        metadata = result["metadata"]
        self.assertEqual(metadata["method"], "provided_research_labels")
        self.assertFalse(metadata["inference_executed"])
        self.assertIsNone(metadata["model"])
        self.assertFalse(metadata["label_mapping_verified"])
        self.assertIsNone(metadata["anatomical_label"])
        self.assertFalse(metadata["annotation_authorship_verified"])
        self.assertEqual(metadata["units"], "voxel")
        self.assertIsNone(metadata["spacing_mm"])
        self.assertEqual(metadata["source_sha256"], "5edb6b44e0113765feb64fb3567f120cfa2311e2b8aa2a51814bc88dcde34860")
        self.assertTrue(metadata["image_alignment_verified"])

    def test_tampered_label_is_rejected_without_replacing_it(self):
        path = self.data / self.case["provided_labels"]
        raw = path.read_bytes()
        path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        with self.assertRaisesRegex(ValueError, "SHA256"):
            load_reference_mask(self.data, self.case)
        with self.assertRaisesRegex(ValueError, "SHA256"):
            prepare_reference_labels(self.data, ROOT)

    def test_source_image_order_and_pixels_are_verified(self):
        case = copy.deepcopy(self.case)
        case["slices"] = list(reversed(case["slices"]))
        with self.assertRaisesRegex(ValueError, "影像雜湊"):
            load_reference_mask(self.data, case)
        path = self.data / self.case["slices"][0]
        image = np.load(path, allow_pickle=False)
        image[0, 0] = image[0, 0] + 1
        np.save(path, image, allow_pickle=False)
        with self.assertRaisesRegex(ValueError, "影像雜湊"):
            load_reference_mask(self.data, self.case)

    def test_bounds_class_and_shape_checks(self):
        for value in (-1, 4, True, 1., "1", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                load_reference_mask(self.data, self.case, value)
        for update in ({"provided_labels": "../outside.npy"}, {"id": "other-case"}, {"modality": "CT"}, {"slices": self.case["slices"][:-1]}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                load_reference_mask(self.data, self.case | update)
        changed = copy.deepcopy(self.case)
        changed["provided_labels_meta"]["shape_zyx"] = [7, 512, 428]
        with self.assertRaises(ValueError):
            load_reference_mask(self.data, changed)

    def test_preparation_is_idempotent_and_copies_exact_label_file(self):
        again = prepare_reference_labels(self.data, ROOT)
        self.assertEqual(again, {key: self.case[key] for key in ("provided_labels", "provided_labels_meta")})
        source = ROOT / "assets/mri/reference_labels.npy"
        destination = self.data / again["provided_labels"]
        self.assertEqual(source.read_bytes(), destination.read_bytes())
        self.assertEqual(hashlib.sha256(destination.read_bytes()).hexdigest(), again["provided_labels_meta"]["sha256"])


if __name__ == "__main__":
    unittest.main()
