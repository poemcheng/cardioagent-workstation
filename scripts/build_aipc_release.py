"""Build a clean Windows CPython 3.11 x64 release, with verified local wheels.

Uses tracked source paths, never an indiscriminate copy of the work folder.
Weights, drivers, credentials, user databases and previous target evidence are
excluded. The finished archive is for software deployment, not AMD certification.
"""
import argparse
from datetime import datetime,timezone
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path,PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cardio import __version__
from scripts.verify_package import verify


def normalized(name):return re.sub(r'[-_.]+','-',name).lower()


def required_pins(root,with_vision=True):
    files=['requirements.txt']+(['requirements-vision.txt'] if with_vision else [])
    pins={}
    for name in files:
        for line in (root/name).read_text(encoding='utf-8').splitlines():
            if not line.strip() or line.startswith(('#','-r')):continue
            match=re.fullmatch(r'([\w.-]+)==([\w.+-]+)(?:\s*;\s*sys_platform\s*==\s*[\'\"]win32[\'\"])?',line.strip())
            if not match:raise ValueError('Unsupported dependency pin: '+line)
            pins[normalized(match[1])]=match[2]
    return pins


def wheel_inventory(root,with_vision=True):
    pins=required_pins(root,with_vision);found={}
    for path in sorted((root/'wheelhouse').glob('*.whl')):
        parts=path.name[:-4].split('-')
        if len(parts)<5:continue
        name,version=normalized(parts[0]),parts[1]
        if pins.get(name)!=version:continue
        python_tag,abi,platform=parts[-3:]
        pure=platform=='any' and abi=='none' and ('py3' in python_tag.split('.'))
        native=platform=='win_amd64' and (python_tag=='cp311' or (abi=='abi3' and re.fullmatch(r'cp3\d+',python_tag) and int(python_tag[2:])<=311))
        if not (pure or native):continue
        with zipfile.ZipFile(path) as wheel:
            metadata=[p for p in wheel.namelist() if p.endswith('.dist-info/METADATA')]
            if len(metadata)!=1:raise ValueError('Ambiguous wheel metadata: '+path.name)
            info=BytesParser().parsebytes(wheel.read(metadata[0]))
            if normalized(info['Name'])!=name or info['Version']!=version:raise ValueError('Wheel identity mismatch: '+path.name)
            if wheel.testzip():raise ValueError('Corrupt wheel: '+path.name)
        if name in found:raise ValueError('Duplicate target wheel: '+name)
        found[name]={'path':path.name,'name':name,'version':version,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size}
    missing=sorted(set(pins)-set(found))
    if missing:raise ValueError('Missing Windows CPython 3.11 wheels: '+', '.join(missing))
    return list(found.values())


def permitted(name):
    path=PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:return False
    if not path.parts:return False
    if path.parts[0] in {'.git','.github','.venv','data','dist','evidence','evidence_v1','wheelhouse','release_evidence'}:return False
    if any(p in {'__pycache__','node_modules'} or p.startswith('integrated_demo_data') or p.startswith('demo_data') for p in path.parts):return False
    if path.name in {'PACKAGE_MANIFEST.json','AIPC_DISTRIBUTION.json','AIPC_DISTRIBUTION_MANIFEST.json','RELEASE_INFO.json','session.key'}:return False
    if path.name.startswith('.env') or path.suffix.lower() in {'.sqlite3','.sqlite','.db','.key','.pem','.pfx','.onnx','.gguf','.h5','.pyc'}:return False
    if path.parts[0]=='models' and path.name!='README.md':return False
    return True


def tracked_sources(root):
    result=subprocess.run(['git','ls-files','-z'],cwd=root,check=True,capture_output=True)
    return [p.decode('utf-8') for p in result.stdout.split(b'\0') if p]


def build(output,source_paths=None,source_commit=None,with_vision=True,root=ROOT):
    root=Path(root).resolve();output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    paths=tracked_sources(root) if source_paths is None else list(source_paths)
    if source_commit is None:
        try:source_commit=subprocess.run(['git','rev-parse','HEAD'],cwd=root,check=True,capture_output=True,text=True).stdout.strip()
        except (OSError,subprocess.SubprocessError):pass
    if source_commit is not None and not re.fullmatch('[a-f0-9]{40}',source_commit):raise ValueError('source_commit must be a full Git commit SHA')
    wheels=wheel_inventory(root,with_vision)
    stamp=datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(prefix='cardio-release-') as temp:
        stage=Path(temp)/'CardioAgent_AMD_AIPC';stage.mkdir()
        for name in sorted(set(paths)):
            if not permitted(name):continue
            path=(root/name).resolve()
            if not path.is_relative_to(root) or not path.is_file():raise ValueError('Invalid source path: '+name)
            target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
        for needed in ('run_demo.py','start_aipc_windows.cmd','accept_aipc_windows.cmd','scripts/accept_aipc.py','docs/cardio-runtime.zip','datasets/mts_dialog/manifest.json'):
            if not (stage/needed).is_file():raise ValueError('Release source is incomplete: '+needed)
        # Generated text is source-pinned and validated by prepare_aipc_data.py.
        cases=root/'datasets/mts_dialog/cases.jsonl'
        manifest=json.loads((root/'datasets/mts_dialog/manifest.json').read_text(encoding='utf-8'))
        if not cases.is_file() or hashlib.sha256(cases.read_bytes()).hexdigest()!=manifest['cases_sha256']:raise ValueError('Prepare and verify the text data first.')
        shutil.copyfile(cases,stage/'datasets/mts_dialog/cases.jsonl')
        (stage/'wheelhouse').mkdir()
        for item in wheels:shutil.copyfile(root/'wheelhouse'/item['path'],stage/'wheelhouse'/item['path'])
        # The release always starts with an empty model selection and loopback URL.
        (stage/'config.json').write_text(json.dumps({'lemonade_url':'http://127.0.0.1:13305/v1','planner_model':'','planner_timeout_s':45,
            'vision_provider':'CPUExecutionProvider','confidence':.25,'dictation_model':''},indent=2)+'\n',encoding='utf-8')
        evidence=root/'release_evidence'
        if evidence.is_dir():
            (stage/'release_evidence').mkdir()
            for p in evidence.rglob('*'):
                if p.is_file() and p.suffix in {'.json','.log','.txt','.html'}:
                    dest=stage/'release_evidence'/p.relative_to(evidence);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
        info={'schema':'cardio-release/1','version':__version__,'created_at':stamp,'source_commit':source_commit,
            'target':'Windows 11 x64 / CPython 3.11 x64','wheel_inventory':wheels,'with_cpu_onnx_runtime':with_vision,
            'public_dialogues':manifest['public_dialogues'],'synthetic_cases':manifest['synthetic_cases'],
            'cases_sha256':manifest['cases_sha256'],'config_reset_to_loopback_defaults':True,
            'weights_bundled':False,'amd_drivers_bundled':False,'npu_execution_verified':False,'clinical_effectiveness_measured':False}
        (stage/'RELEASE_INFO.json').write_text(json.dumps(info,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        entries=[{'path':p.relative_to(stage).as_posix(),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
                 for p in sorted(stage.rglob('*')) if p.is_file()]
        (stage/'PACKAGE_MANIFEST.json').write_text(json.dumps({'schema':'cardio-package/2','created_at':stamp,'files':entries},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        checked=verify(stage)
        if not checked['valid']:raise ValueError('Staged package verification failed: '+str(checked['failed']))
        suffix='-'+source_commit[:7] if source_commit else '-local'
        archive=output/f'CardioAgent_AMD_AIPC_v{__version__}{suffix}_Windows_Py311.zip'
        if archive.exists():raise FileExistsError('Archive already exists; select a new output directory.')
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=7) as bundle:
            for path in sorted(stage.rglob('*')):
                if path.is_file():bundle.write(path,'CardioAgent_AMD_AIPC/'+path.relative_to(stage).as_posix())
        with zipfile.ZipFile(archive) as bundle:
            if bundle.testzip():raise ValueError('Archive CRC verification failed.')
        digest=hashlib.sha256(archive.read_bytes()).hexdigest()
        archive.with_suffix('.zip.sha256').write_text(digest+'  '+archive.name+'\n',encoding='utf-8')
        return {'archive':str(archive),'sha256':digest,'bytes':archive.stat().st_size,'source_commit':source_commit,
                'verified_files':checked['files'],'windows_wheels':len(wheels),'npu_execution_verified':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'dist')
    parser.add_argument('--source-list',type=Path,help='Explicit JSON path list when building a non-git checkout.')
    parser.add_argument('--source-commit');parser.add_argument('--core-only',action='store_true')
    args=parser.parse_args(argv)
    paths=json.loads(args.source_list.read_text(encoding='utf-8')) if args.source_list else None
    print(json.dumps(build(args.output,paths,args.source_commit,not args.core_only),ensure_ascii=False,indent=2))


if __name__=='__main__':raise SystemExit(main())
