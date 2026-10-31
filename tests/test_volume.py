"""Geometry, mesh and reconstruction checks; no clinical accuracy claims."""
import hashlib
import io
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import numpy as np

from cardio.imaging import read_upload
from cardio.volume import (assemble_geometry, generate_phantom, mesh_to_obj,
                           mesh_to_stl, reconstruct)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def slice_meta(z, marker=0, **updates):
    geometry = {
        "pixel_spacing_yx": [0.7, 0.5],
        "position_xyz": [10.0, 20.0, float(z)],
        "orientation": [1., 0., 0., 0., 1., 0.],
        "series_hash": digest("series"), "study_hash": digest("study"),
        "frame_hash": digest("frame"), "rows": 32, "columns": 32,
    }
    geometry.update(updates)
    return np.full((32, 32), marker, dtype=np.float32), {
        "modality": "CT", "is_hu": True, "geometry": geometry,
    }


class DicomGeometryTests(unittest.TestCase):
    def test_sort_uses_patient_position_and_correct_xyz_spacing(self):
        parsed = [slice_meta(34, 3), slice_meta(30, 1), slice_meta(32, 2)]
        ordered, geometry = assemble_geometry(parsed)
        self.assertTrue(geometry["verified"])
        self.assertEqual(geometry["units"], "mm")
        self.assertEqual(geometry["coordinate_system"], "DICOM_LPS")
        np.testing.assert_allclose(geometry["spacing_xyz"], [.5, .7, 2.])
        np.testing.assert_allclose(geometry["origin_xyz"], [10., 20., 30.])
        np.testing.assert_allclose(geometry["direction_xyz"], np.eye(3))
        self.assertEqual([float(arr[0, 0]) for arr, _ in ordered], [1., 2., 3.])
        self.assertEqual(geometry["shape_zyx"], [3, 32, 32])

    def test_oblique_orientation_sorts_along_normal(self):
        # x is patient +y; y is patient +z; their cross product is patient +x.
        orientation = [0., 1., 0., 0., 0., 1.]
        parsed = [slice_meta(30, marker, orientation=orientation,
                             position_xyz=[x, 20., 30.])
                  for x, marker in [(14., 3), (10., 1), (12., 2)]]
        ordered, geometry = assemble_geometry(parsed)
        self.assertTrue(geometry["verified"])
        np.testing.assert_allclose(geometry["direction_xyz"],
                                   [[0., 0., 1.], [1., 0., 0.], [0., 1., 0.]])
        self.assertEqual([float(arr[0, 0]) for arr, _ in ordered], [1., 2., 3.])

    def test_missing_geometry_does_not_invent_millimetres(self):
        parsed = [(np.zeros((32, 32)), {"modality": "MRI"}) for _ in range(3)]
        ordered, geometry = assemble_geometry(parsed)
        self.assertEqual(len(ordered), 3)
        self.assertFalse(geometry["verified"])
        self.assertEqual(geometry["units"], "voxel")
        self.assertTrue(geometry["issues"])

    def test_single_slice_cannot_establish_slice_spacing(self):
        _, geometry = assemble_geometry([slice_meta(30)])
        self.assertFalse(geometry["verified"])
        self.assertEqual(geometry["units"], "voxel")

    def test_duplicate_and_nonuniform_positions_are_not_physical_geometry(self):
        for positions in ((30, 30, 32), (30, 32, 37)):
            with self.subTest(positions=positions):
                _, geometry = assemble_geometry([slice_meta(z) for z in positions])
                self.assertFalse(geometry["verified"])
                self.assertEqual(geometry["units"], "voxel")

    def test_series_study_or_frame_mixing_is_not_verified(self):
        for key in ("series_hash", "study_hash", "frame_hash"):
            with self.subTest(key=key):
                parsed = [slice_meta(30), slice_meta(32), slice_meta(34)]
                parsed[1][1]["geometry"][key] = digest("different")
                _, geometry = assemble_geometry(parsed)
                self.assertFalse(geometry["verified"])
                self.assertEqual(geometry["units"], "voxel")

    def test_orientation_spacing_and_inplane_skew_are_checked(self):
        changes = [
            {"orientation": [1., 0., 0., .2, 1., 0.]},
            {"orientation": [2., 0., 0., 0., 1., 0.]},
            {"orientation": [0., 1., 0., 1., 0., 0.]},
            {"pixel_spacing_yx": [.8, .5]},
            {"position_xyz": [11., 20., 32.]},
            {"rows": 33},
        ]
        for change in changes:
            with self.subTest(change=change):
                parsed = [slice_meta(30), slice_meta(32, **change), slice_meta(34)]
                _, geometry = assemble_geometry(parsed)
                self.assertFalse(geometry["verified"])
                self.assertEqual(geometry["units"], "voxel")

    def test_nonfinite_or_malformed_metadata_fails_closed(self):
        for change in ({"position_xyz": [0., 0., float("nan")]},
                       {"pixel_spacing_yx": [-1., 1.]},
                       {"orientation": [1., 0.]},
                       {"position_xyz": "not-a-coordinate"}):
            with self.subTest(change=change):
                _, geometry = assemble_geometry([slice_meta(30), slice_meta(32, **change)])
                self.assertFalse(geometry["verified"])
                self.assertEqual(geometry["units"], "voxel")
                json.dumps(geometry, allow_nan=False)

    def test_upload_keeps_geometry_and_hashes_uids_without_patient_header(self):
        from pydicom.dataset import FileDataset, FileMetaDataset
        from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid
        fm = FileMetaDataset()
        fm.TransferSyntaxUID = ExplicitVRLittleEndian
        fm.MediaStorageSOPClassUID = CTImageStorage
        fm.MediaStorageSOPInstanceUID = generate_uid()
        ds = FileDataset(None, {}, file_meta=fm, preamble=b"\0" * 128)
        ds.Modality = "CT"
        ds.Rows = ds.Columns = 32
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated = ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 0
        ds.RescaleSlope, ds.RescaleIntercept = 1, -1024
        ds.PixelSpacing = [.7, .5]
        ds.ImagePositionPatient = [10, 20, 30]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.SeriesInstanceUID = generate_uid()
        ds.StudyInstanceUID = generate_uid()
        ds.FrameOfReferenceUID = generate_uid()
        ds.PatientName = "PRIVATE-PATIENT"
        ds.PatientID = "PRIVATE-ID"
        ds.PixelData = np.arange(1024, dtype=np.uint16).reshape(32, 32).tobytes()
        out = io.BytesIO()
        ds.save_as(out, enforce_file_format=True)
        pixels, metadata = read_upload(out.getvalue(), "PRIVATE-FILENAME.dcm", "CT")
        self.assertEqual(pixels[0, 0], -1024.)
        geometry = metadata["geometry"]
        np.testing.assert_allclose(geometry["pixel_spacing_yx"], [.7, .5])
        self.assertEqual(geometry["position_xyz"], [10., 20., 30.])
        for key, value in (("series_hash", ds.SeriesInstanceUID),
                           ("study_hash", ds.StudyInstanceUID),
                           ("frame_hash", ds.FrameOfReferenceUID)):
            self.assertEqual(len(geometry[key]), 64)
            self.assertNotEqual(geometry[key], str(value))
            self.assertNotIn(str(value), json.dumps(metadata))
        self.assertNotIn("PRIVATE", json.dumps(metadata))


class ReconstructionTests(unittest.TestCase):
    @staticmethod
    def arrays():
        volume = np.zeros((3, 3, 3), np.float32)
        volume[1, 1, 1] = 200
        return list(volume)

    @staticmethod
    def geometry():
        return {"verified": True, "units": "mm", "spacing_xyz": [2., 3., 4.],
                "origin_xyz": [10., 20., 30.], "direction_xyz": np.eye(3).tolist(),
                "coordinate_system": "DICOM_LPS", "shape_zyx": [3, 3, 3],
                "issues": []}

    def test_physical_volume_is_original_mask_count_times_voxel_volume(self):
        arrays = self.arrays()
        result = reconstruct(arrays, {"geometry": self.geometry()}, 120)
        self.assertEqual(result["statistics"]["foreground_voxels"], 1)
        self.assertEqual(result["statistics"]["physical_volume_mm3"], 24.)
        self.assertEqual(result["mesh"]["units"], "mm")
        self.assertEqual(result["segmentation"]["method"], "intensity_threshold")
        vertices = np.asarray(result["mesh"]["vertices"])
        np.testing.assert_allclose(vertices.min(axis=0), [11., 21.5, 32.])
        np.testing.assert_allclose(vertices.max(axis=0), [13., 24.5, 36.])

    def test_supplied_learned_mask_controls_volume_without_anatomy_verification(self):
        arrays = [np.zeros((3, 3), dtype=np.float32) for _ in range(3)]
        mask = np.zeros((3, 3, 3), dtype=bool)
        mask[1, 1, 1] = True
        metadata = {"method": "registered_onnx", "model_sha256": "a" * 64,
                    "class_label": "declared-label", "preprocessing": {"scale": 1 / 255}}
        result = reconstruct(arrays, {"geometry": self.geometry()}, 120,
                             mask=mask, mask_metadata=metadata)
        self.assertEqual(result["statistics"]["foreground_voxels"], 1)
        self.assertEqual(result["statistics"]["physical_volume_mm3"], 24.)
        self.assertTrue(result["mesh"]["faces"])
        segmentation = result["segmentation"]
        self.assertEqual(segmentation["method"], "registered_onnx")
        self.assertFalse(segmentation["class_label_verified"])
        kept = segmentation["metadata"]
        for key in ("model_sha256", "class_label", "preprocessing"):
            self.assertEqual(kept[key], metadata[key])

    def test_supplied_mask_must_match_volume_shape_and_be_boolean(self):
        for mask in (np.zeros((2, 3, 3), dtype=bool),
                     np.zeros((3, 3, 3), dtype=np.float32)):
            with self.subTest(shape=mask.shape, dtype=mask.dtype), self.assertRaises(ValueError):
                reconstruct(self.arrays(), {}, 120, mask=mask,
                            mask_metadata={"method": "registered_onnx"})

    def test_rotated_patient_axes_are_applied(self):
        geometry = self.geometry()
        geometry["direction_xyz"] = [[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]]
        result = reconstruct(self.arrays(), {"geometry": geometry}, 120)
        vertices = np.asarray(result["mesh"]["vertices"])
        np.testing.assert_allclose(vertices.min(axis=0), [5.5, 21., 32.])
        np.testing.assert_allclose(vertices.max(axis=0), [8.5, 23., 36.])

    def test_unknown_geometry_has_voxel_mesh_and_null_physical_volume(self):
        result = reconstruct(self.arrays(), {}, 120)
        self.assertEqual(result["mesh"]["units"], "voxel")
        self.assertIsNone(result["statistics"]["physical_volume_mm3"])
        vertices = np.asarray(result["mesh"]["vertices"])
        np.testing.assert_allclose(vertices.min(axis=0), [.5, .5, .5])
        np.testing.assert_allclose(vertices.max(axis=0), [1.5, 1.5, 1.5])

    def test_invalid_or_unverified_geometry_cannot_produce_physical_volume(self):
        variants = [
            {"verified": False}, {"spacing_xyz": [-2., 3., 4.]},
            {"spacing_xyz": [2., float("nan"), 4.]},
            {"direction_xyz": [[1., .2, 0.], [0., 1., 0.], [0., 0., 1.]]},
            {"shape_zyx": [3, 30, 30]},
        ]
        for change in variants:
            with self.subTest(change=change):
                geometry = self.geometry() | change
                try:
                    result = reconstruct(self.arrays(), {"geometry": geometry}, 120)
                except ValueError:
                    continue
                self.assertEqual(result["mesh"]["units"], "voxel")
                self.assertIsNone(result["statistics"]["physical_volume_mm3"])
                json.dumps(result, allow_nan=False)

    def test_single_voxel_mesh_is_closed_and_has_twelve_triangles(self):
        mesh = reconstruct(self.arrays(), {}, 120)["mesh"]
        self.assertEqual(len(mesh["faces"]), 12)
        vertices = np.asarray(mesh["vertices"])
        edges = Counter()
        for face in mesh["faces"]:
            self.assertEqual(len(face), 3)
            self.assertEqual(len(set(face)), 3)
            self.assertTrue(all(0 <= idx < len(vertices) for idx in face))
            points = [tuple(vertices[idx]) for idx in face]
            self.assertGreater(np.linalg.norm(np.cross(vertices[face[1]] - vertices[face[0]],
                                                       vertices[face[2]] - vertices[face[0]])), 0)
            for a, b in zip(points, points[1:] + points[:1]):
                edges[tuple(sorted((a, b)))] += 1
        self.assertTrue(all(count == 2 for count in edges.values()))

    def test_downsampling_does_not_change_original_volume_statistics(self):
        volume = np.zeros((8, 20, 20), np.float32)
        volume[:, 2:18, 3:17] = 200
        geometry = self.geometry()
        geometry["shape_zyx"] = list(volume.shape)
        result = reconstruct(list(volume), {"geometry": geometry}, 120, max_axis=8)
        expected = int(np.count_nonzero(volume >= 120))
        self.assertEqual(result["statistics"]["foreground_voxels"], expected)
        self.assertEqual(result["statistics"]["physical_volume_mm3"], expected * 24.)
        self.assertTrue(result["mesh"]["vertices"])

    def test_empty_threshold_result_is_empty_mesh_not_invented_anatomy(self):
        result = reconstruct(self.arrays(), {}, 255)
        self.assertEqual(result["statistics"]["foreground_voxels"], 0)
        self.assertEqual(result["mesh"]["vertices"], [])
        self.assertEqual(result["mesh"]["faces"], [])

    def test_nonfinite_threshold_and_inconsistent_volume_are_rejected(self):
        for threshold in (float("nan"), float("inf"), "invalid"):
            with self.subTest(threshold=threshold), self.assertRaises((ValueError, TypeError)):
                reconstruct(self.arrays(), {}, threshold)
        with self.assertRaises(ValueError):
            reconstruct([np.zeros((3, 3)), np.zeros((4, 3))], {}, 120)

    def test_exports_retain_units_and_only_valid_mesh_coordinates(self):
        mesh = reconstruct(self.arrays(), {"geometry": self.geometry()}, 120)["mesh"]
        obj = mesh_to_obj(mesh)
        stl = mesh_to_stl(mesh)
        self.assertIsInstance(obj, str)
        self.assertIsInstance(stl, str)
        self.assertIn("mm", obj)
        self.assertIn("mm", stl)
        self.assertEqual(sum(line.startswith("f ") for line in obj.splitlines()), 12)
        self.assertEqual(stl.count("facet normal"), 12)
        self.assertNotIn("nan", obj.lower())
        self.assertNotIn("nan", stl.lower())

    def test_phantom_truth_is_independent_analytic_mask_with_known_spacing(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            case = generate_phantom(data)
            self.assertEqual(case["id"], "demo-volume-phantom")
            self.assertTrue(case["synthetic"])
            self.assertTrue(case["geometry"]["verified"])
            self.assertEqual(case["geometry"]["coordinate_system"], "synthetic_world")
            arrays = [np.load(data / p, allow_pickle=False) for p in case["slices"]]
            self.assertEqual(np.asarray(arrays).shape, (32, 96, 96))
            reference = np.load(data / case["reference_mask"], allow_pickle=False).astype(bool)
            predicted = np.asarray(arrays) >= 120
            dice = 2 * np.count_nonzero(reference & predicted) / (np.count_nonzero(reference) + np.count_nonzero(predicted))
            self.assertEqual(dice, 1.)
            result = reconstruct(arrays, case, 120)
            self.assertEqual(result["statistics"]["foreground_voxels"], int(reference.sum()))
            self.assertGreater(result["statistics"]["physical_volume_mm3"], 0)
            serialized = json.dumps(case, ensure_ascii=False).lower()
            self.assertTrue("phantom" in serialized or "體模" in serialized)
            self.assertIn("reference_model", case)
            self.assertNotIn("patientname", serialized)
            for path in [*case["slices"], case["reference_mask"]]:
                self.assertTrue((data / path).resolve().is_relative_to(data.resolve()))


if __name__ == "__main__":
    unittest.main()
