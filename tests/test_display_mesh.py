"""Display improvements must preserve the measured and exported source."""
import hashlib
import unittest
import numpy as np
from cardio.volume import _mesh, reconstruct


class DisplayMeshTests(unittest.TestCase):
    def setUp(self):
        self.mask = np.zeros((8, 100, 100), dtype=bool)
        self.mask[1:7, 40:60, 30:50] = True
        self.arrays = list(np.zeros(self.mask.shape, np.float32))

    def result(self, **case):
        return reconstruct(self.arrays, case, 128, mask=self.mask,
                           mask_metadata={'method': 'provided_reference'})

    def test_display_ratio_never_changes_measurements_or_source_mesh(self):
        ordinary = self.result()
        scaled = self.result(display_geometry={'reference_grid_matched': True,
            'scale_ratio_xyz': [1, 1, 14.222222222222221]})
        self.assertEqual(ordinary['mesh'], scaled['mesh'])
        self.assertEqual(ordinary['statistics'], scaled['statistics'])
        self.assertIsNone(scaled['statistics']['physical_volume_mm3'])
        self.assertFalse(scaled['geometry']['verified'])
        self.assertEqual(scaled['mesh']['units'], 'voxel')
        self.assertFalse(scaled['display_mesh']['display_scale_verified'])
        self.assertEqual(scaled['display_mesh']['source_mask_sha256'],
                         hashlib.sha256(self.mask.tobytes()).hexdigest())

    def test_roi_keeps_global_source_coordinates_before_smoothing(self):
        result = self.result()
        roi = _mesh(self.mask, result['geometry'], 128, crop=True)
        full = _mesh(self.mask, result['geometry'], 128)
        np.testing.assert_allclose(np.min(roi['vertices'], axis=0),
                                   np.min(full['vertices'], axis=0))
        np.testing.assert_allclose(np.max(roi['vertices'], axis=0),
                                   np.max(full['vertices'], axis=0))
        self.assertTrue(result['display_mesh']['display_only'])
        self.assertFalse(result['display_mesh']['measurement_source'])
        self.assertGreater(result['display_mesh']['smoothing']['max_displacement_source_units'], 0)

    def test_unknown_or_invalid_display_ratio_is_not_applied(self):
        for ratio in ([1, 1, float('nan')], [0, 1, 1], [1, 1, 1001], [1, 1]):
            with self.subTest(ratio=ratio):
                result = self.result(display_geometry={'reference_grid_matched': True, 'scale_ratio_xyz': ratio})
                self.assertNotIn('display_scale_xyz', result['display_mesh'])
        self.assertNotIn('display_scale_xyz', self.result(display_geometry={
            'reference_grid_matched': False, 'scale_ratio_xyz': [1, 1, 14]})['display_mesh'])

    def test_empty_mask_remains_empty_in_both_surfaces(self):
        self.mask[:] = False
        result = self.result()
        for name in ('mesh', 'display_mesh'):
            self.assertEqual(result[name]['vertices'], [])
            self.assertEqual(result[name]['faces'], [])

    def test_interpolated_surface_is_finite_closed_and_oriented_outward(self):
        result = self.result(display_geometry={'reference_grid_matched': True,
            'scale_ratio_xyz': [1, 1, 14]})
        mesh = result['display_mesh']
        self.assertEqual(mesh['display_field']['interpolation'], 'trilinear')
        self.assertEqual(mesh['display_field']['surface'], 'marching_tetrahedra')
        vertices, faces = np.array(mesh['vertices']), np.array(mesh['faces'])
        self.assertTrue(np.isfinite(vertices).all())
        self.assertLessEqual(len(faces), 200000)
        edges = np.sort(np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]])), axis=1)
        _, degree = np.unique(edges, axis=0, return_counts=True)
        self.assertTrue(np.all(degree == 2))
        normals = np.cross(vertices[faces[:, 1]] - vertices[faces[:, 0]],
                           vertices[faces[:, 2]] - vertices[faces[:, 0]])
        self.assertTrue(np.all(np.linalg.norm(normals, axis=1) > 1e-12))
        self.assertTrue(np.all(np.einsum('ij,ij->i', normals,
            vertices[faces].mean(axis=1) - vertices.mean(axis=0)) > 0))


if __name__ == '__main__':
    unittest.main()
