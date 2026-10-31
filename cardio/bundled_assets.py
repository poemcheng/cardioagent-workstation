"""Restore verified demo arrays from the public build after a source checkout."""
import hashlib
import json
from pathlib import Path
import zipfile


def ensure_demo_assets(root):
    root=Path(root)
    if not (root/'assets/mri/meta.json').is_file():
        return False
    names=[f'assets/mri/{i:04}.npy' for i in range(8)]+['assets/mri/reference_labels.npy']
    if all((root/name).is_file() for name in names):
        return False
    archive=root/'docs/cardio-runtime.zip'
    manifest=root/'docs/browser-manifest.json'
    if not archive.is_file() or not manifest.is_file():
        raise ValueError('研究影像陣列缺漏；請完整下載專案，包含 docs/cardio-runtime.zip。')
    expected=json.loads(manifest.read_text())['runtime_sha256']
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:
        raise ValueError('研究影像來源套件 SHA256 不符，未還原資料。')
    with zipfile.ZipFile(archive) as z:
        for name in names:
            info=z.getinfo(name)
            if info.file_size>32*1024*1024:
                raise ValueError('研究影像陣列超過大小上限。')
        for name in names:
            target=root/name;target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists():
                target.write_bytes(z.read(name))
    return True
