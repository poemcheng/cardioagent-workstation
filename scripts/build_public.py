"""Build GitHub Pages with the actual Python pipeline, not saved result stubs."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT/'web'
DEST = ROOT/'docs'
WHEELS = ('flask','werkzeug','jinja2','itsdangerous','click','blinker','pydicom')
REDACT = {'drive_file_id','drive_url','source_drive_file_id','source_drive_url'}


def public_metadata(value):
    if isinstance(value, dict):
        return {k: public_metadata(v) for k,v in value.items() if k not in REDACT}
    if isinstance(value, list):
        return [public_metadata(v) for v in value]
    return value


def main():
    DEST.mkdir(exist_ok=True)
    for source in WEB.iterdir():
        if source.is_file():
            shutil.copy2(source,DEST/source.name)
    shutil.copytree(ROOT/'static',DEST/'static',dirs_exist_ok=True)
    (DEST/'.nojekyll').write_text('')
    archive=DEST/'cardio-runtime.zip'
    members=[]
    for folder in ('cardio','templates','assets','models'):
        members.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc','.h5','.onnx'})
    members.append(ROOT/'config.json')
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(members):
            name=p.relative_to(ROOT).as_posix()
            info=zipfile.ZipInfo(name,date_time=(2026,10,31,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            if p.suffix=='.json':
                z.writestr(info,json.dumps(public_metadata(json.loads(p.read_text())),ensure_ascii=False,indent=2)+'\n')
            else:
                z.writestr(info,p.read_bytes())
    wheel_dir=DEST/'wheels';wheel_dir.mkdir(exist_ok=True)
    selected=[]
    for name in WHEELS:
        choices=list((ROOT/'wheelhouse').glob(name+'-*-py3-none-any.whl'))
        if not choices:
            choices=list(wheel_dir.glob(name+'-*-py3-none-any.whl'))
        if not choices:
            raise SystemExit(f'Missing pure-Python wheel: {name}; supply it before building.')
        p=sorted(choices)[-1]
        if p.resolve()!=(wheel_dir/p.name).resolve():
            shutil.copy2(p,wheel_dir/p.name)
        selected.append(p.name)
    manifest={'schema':'cardio-browser/1','version':'2.1.0','pyodide_version':'0.29.4',
        'runtime_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
        'runtime_bytes':archive.stat().st_size,'wheels':selected,
        'pipeline':'original Python Flask/NumPy inside browser Web Worker',
        'results_are_precomputed':False,'data_persistence':'this_tab_only',
        'llm_or_clinical_models_bundled':False,'amd_npu_execution':False}
    (DEST/'browser-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'version':'2.1.0','runtime_bytes':archive.stat().st_size,'python_source_files':len(members),'pure_python_wheels':len(selected),'output':str(DEST)}))


if __name__=='__main__':
    main()
