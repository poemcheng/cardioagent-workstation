import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from cardio.bundled_assets import ensure_demo_assets


class BundledAssetsTests(unittest.TestCase):
    def fixture(self,root):
        (root/'assets/mri').mkdir(parents=True);(root/'assets/mri/meta.json').write_text('{}')
        (root/'docs').mkdir();archive=root/'docs/cardio-runtime.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for name in [f'assets/mri/{i:04}.npy' for i in range(8)]+['assets/mri/reference_labels.npy']:
                z.writestr(name,b'verified fixture')
            z.writestr('../outside-file',b'never extracted')
        (root/'docs/browser-manifest.json').write_text(json.dumps({'runtime_sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}))
        return archive

    def test_extracts_only_expected_arrays_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.fixture(root)
            self.assertTrue(ensure_demo_assets(root));self.assertFalse(ensure_demo_assets(root))
            self.assertEqual((root/'assets/mri/0000.npy').read_bytes(),b'verified fixture')
            self.assertFalse((root.parent/'outside-file').exists())

    def test_corrupt_archive_is_rejected_before_writing_arrays(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);archive=self.fixture(root);archive.write_bytes(archive.read_bytes()+b'changed')
            with self.assertRaises(ValueError):ensure_demo_assets(root)
            self.assertFalse((root/'assets/mri/0000.npy').exists())

if __name__=='__main__':unittest.main()
