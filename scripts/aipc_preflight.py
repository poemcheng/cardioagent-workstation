"""Verify downloaded data and a loopback model service; do not infer NPU use."""
import argparse
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cardio.planner import Planner


def check(configure=False,model_id='qwen3.5-4b-FLM'):
    path=ROOT/'datasets/mts_dialog/manifest.json';cases=path.with_name('cases.jsonl')
    manifest=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    ready=cases.is_file() and hashlib.sha256(cases.read_bytes()).hexdigest()==manifest.get('cases_sha256')
    report={'schema':'cardio-aipc-preflight/1','created_at':datetime.now(timezone.utc).isoformat(),
            'host':{'os':platform.system(),'os_release':platform.release(),'architecture':platform.machine()},
            'dataset_verified':ready,'public_dialogues':manifest.get('public_dialogues'),'synthetic_cases':manifest.get('synthetic_cases'),
            'baseline_ready':ready,'local_llm_ready':False,'selected_model':model_id,'models':[],
            'npu_execution_verified':False,'model_weights_bundled':False,'probe_errors':[]}
    config_path=ROOT/'config.json';config=json.loads(config_path.read_text(encoding='utf-8'))
    configured=config.get('lemonade_url','http://127.0.0.1:13305/v1')
    endpoints=list(dict.fromkeys([configured,'http://127.0.0.1:13305/api/v1','http://127.0.0.1:13305/v1']))
    for endpoint in endpoints:
        try:
            planner=Planner(config|{'lemonade_url':endpoint});payload=planner.models()
            if not isinstance(payload,dict) or not isinstance(payload.get('data'),list):raise ValueError('models.data must be a list')
            models=[{k:m.get(k) for k in ('id','recipe','downloaded')} for m in payload['data'] if planner.is_local(m)]
            report.update(endpoint=endpoint,models=models,local_llm_ready=ready and any(m['id']==model_id for m in models))
            if configure and report['local_llm_ready']:
                from scripts.configure_lemonade import write_config
                write_config(config_path,config|{'lemonade_url':endpoint,'planner_model':model_id})
                report['configuration_saved']=True
            break
        except Exception as exc:report['probe_errors'].append({'endpoint':endpoint,'error':str(exc)[:200]})
    report['next_step']='Run the local model and collect target-device evidence.' if report['local_llm_ready'] else 'Start Lemonade, enable FLM and download the selected model. The explicit CPU rule baseline is available when baseline_ready is true.'
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--configure',action='store_true');parser.add_argument('--model',default='qwen3.5-4b-FLM')
    parser.add_argument('--output',type=Path,default=ROOT/'evidence/aipc_preflight.json')
    args=parser.parse_args(argv);result=check(args.configure,args.model)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['baseline_ready'] else 1


if __name__=='__main__':raise SystemExit(main())
