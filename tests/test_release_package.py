"""Release integrity, platform selection and data-exclusion regressions."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from cardio import __version__
from scripts.build_aipc_release import permitted, required_pins, wheel_inventory
from scripts.verify_package import verify

ROOT=Path(__file__).resolve().parents[1]


class ReleasePackageTests(unittest.TestCase):
    def test_version_metadata_matches_runtime(self):
        self.assertEqual(json.loads((ROOT/'VERSION.json').read_text())['version'],__version__)

    def test_manifest_rejects_tampering_escape_and_duplicate_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'safe.txt').write_bytes(b'release data')
            item={'path':'safe.txt','bytes':12,'sha256':hashlib.sha256(b'release data').hexdigest()}
            manifest=root/'PACKAGE_MANIFEST.json'
            manifest.write_text(json.dumps({'files':[item]}));self.assertTrue(verify(root)['valid'])
            (root/'safe.txt').write_bytes(b'changed data');self.assertFalse(verify(root)['valid'])
            for entries in ([item|{'path':'../outside.txt'}],[item,item],[item|{'path':'/tmp/outside.txt'}]):
                manifest.write_text(json.dumps({'files':entries}));self.assertFalse(verify(root)['valid'])

    def test_release_does_not_copy_local_records_credentials_or_weights(self):
        for path in ['integrated_demo_data/cardiо.sqlite3','evidence/target_runs/run/acceptance.json','data/session.key',
                     '.env','models/private.gguf','models/detector.onnx','models/detector.json','docs/../../private.txt','/tmp/private.txt']:
            self.assertFalse(permitted(path),path)
        for path in ['cardio/analysis.py','docs/cardio-runtime.zip','datasets/mts_dialog/raw/MTS-Dialog-TrainingSet.csv']:
            self.assertTrue(permitted(path),path)

    def test_missing_or_wrong_platform_wheels_cannot_make_a_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'wheelhouse').mkdir()
            (root/'requirements.txt').write_text('numpy==2.3.5\n')
            (root/'requirements-vision.txt').write_text('-r requirements.txt\n')
            (root/'wheelhouse/numpy-2.3.5-cp311-cp311-manylinux_2_17_x86_64.whl').write_bytes(b'not a target wheel')
            self.assertEqual(required_pins(root),{'numpy':'2.3.5'})
            with self.assertRaisesRegex(ValueError,'Missing Windows'):wheel_inventory(root)
