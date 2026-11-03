import json
from pathlib import Path
import numpy as np


def _display_geometry(root, extras):
    path = root/'assets/mri/display_geometry.json'
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding='utf-8'))
    meta = extras.get('provided_labels_meta', {})
    aligned = [x['file_sha256'] for x in meta.get('image_alignment', {}).get('slices', [])]
    if (value.get('schema') != 'cardio-display-sampling/1' or
            value.get('matched_label_array_sha256') != meta.get('array_sha256') or
            value.get('matched_image_sha256') != aligned):
        raise ValueError('顯示比例與目前研究影像／標籤來源不一致。')
    return {'display_geometry': value}


def seed(store, data, root):
    if store.cases():
        # Enrich existing seeded MRI with its originally supplied research labels.
        # Source pixels are never replaced during this metadata migration.
        existing = next((c for c in store.cases() if c['id'] == 'research-mri-01'), None)
        if existing is not None and (root/'assets/mri/reference_labels_meta.json').exists():
            from .reference_labels import prepare_reference_labels
            from .store import canonical
            extras = prepare_reference_labels(data, root)
            extras.update(_display_geometry(root, extras))
            description = json.loads((root/'assets/mri/meta.json').read_text(encoding='utf-8'))['description']
            if any(existing.get(k) != v for k,v in extras.items()) or existing.get('source_detail') != description:
                existing.update(extras)
                existing['source_detail'] = description
                with store.connect() as db:
                    db.execute('UPDATE cases SET data=? WHERE id=?', (canonical(existing), existing['id']))
        return
    y, x = np.mgrid[:512,:512]
    for zero in (False, True):
        ident = "demo-ct-zero" if zero else "demo-ct-positive"
        folder = data/"images"/ident; folder.mkdir(parents=True, exist_ok=True)
        slices = []
        for i in range(3):
            arr = np.full((512,512), 8, np.float32)
            arr[((x-256)/205)**2+((y-260)/175)**2 < 1] = 78
            for cx in (177,335):
                arr[((x-cx)/58)**2+((y-225)/95)**2 < 1] = 22
            arr[((x-267)/65)**2+((y-286)/70)**2 < 1] = 135
            if not zero:
                arr[(x-(252+i-1))**2+(y-246)**2 <= 16] = 255
                arr[(x-289)**2+(y-(279+i-1))**2 <= 9] = 255
            p = folder/f"{i:04}.npy"; np.save(p,arr,allow_pickle=False); slices.append(str(p.relative_to(data)))
        store.put_case({"id":ident,"label":"合成 CT · 零候選測試" if zero else "合成 CT · 協作流程測試","modality":"CT","is_hu":False,"synthetic":True,"source":"程式幾何合成，非病人影像","slices":slices,"reference_boxes":{} if zero else {"1":[[248,242,257,251],[286,276,293,283]]},"source_detail":"參考框為合成真值，只在獨立開關顯示；不參與推論。"})
    meta = root/"assets/mri/meta.json"
    if meta.exists():
        info=json.loads(meta.read_text(encoding="utf-8"))
        folder=data/"images"/"research-mri-01";folder.mkdir(parents=True,exist_ok=True)
        slices=[]
        for src in sorted((root/"assets/mri").glob("[0-9][0-9][0-9][0-9].npy")):
            p=folder/src.name;p.write_bytes(src.read_bytes());slices.append(str(p.relative_to(data)))
        from .reference_labels import prepare_reference_labels
        label_extras = prepare_reference_labels(data, root)
        label_extras.update(_display_geometry(root, label_extras))
        store.put_case({"id":"research-mri-01","label":"ACDC 心臟 MRI · 研究影像","modality":"MRI","is_hu":False,"synthetic":False,"source":"使用者 Google Drive / ACDC preprocessed volume","slices":slices,"reference_boxes":{},"source_detail":info["description"],"provenance":info["provenance"]} | label_extras)
