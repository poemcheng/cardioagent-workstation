"""Build GitHub Pages with the actual Python pipeline, not saved result stubs."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cardio import __version__
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
    parser = argparse.ArgumentParser()
    parser.add_argument('--full', action='store_true', help='Replace the base archive instead of emitting a verified update')
    options = parser.parse_args()
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
    previous = {}
    if archive.exists() and not options.full:
        with zipfile.ZipFile(archive) as old:
            previous = {name: old.read(name) for name in old.namelist()}
    update = DEST/'cardio-runtime-update.zip'
    target = update if previous else archive
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(members):
            name=p.relative_to(ROOT).as_posix()
            info=zipfile.ZipInfo(name,date_time=(2026,10,31,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            content = (json.dumps(public_metadata(json.loads(p.read_text())),ensure_ascii=False,indent=2)+'\n').encode() if p.suffix=='.json' else p.read_bytes()
            if previous.get(name) != content:
                z.writestr(info,content)
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
    manifest={'schema':'cardio-browser/1','version':__version__,'pyodide_version':'0.29.4',
        'runtime_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
        'runtime_bytes':archive.stat().st_size,'wheels':selected,
        'pipeline':'original Python Flask/NumPy inside browser Web Worker',
        'results_are_precomputed':False,'data_persistence':'this_tab_only',
        'llm_or_clinical_models_bundled':False,'amd_npu_execution':False}
    if previous:
        manifest.update(runtime_update_sha256=hashlib.sha256(update.read_bytes()).hexdigest(),
                        runtime_update_bytes=update.stat().st_size)
    (DEST/'browser-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'version':manifest['version'],'runtime_bytes':archive.stat().st_size,'update_bytes':manifest.get('runtime_update_bytes',0),'python_source_files':len(members),'pure_python_wheels':len(selected),'output':str(DEST)}))


if __name__=='__main__':
    main()
