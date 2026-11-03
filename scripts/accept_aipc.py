"""Run source-bound software acceptance on this PC; optionally use its local LLM.

The image flow uses an authenticated Flask test client, not a browser or TCP
benchmark. Local LLM mode makes real loopback service requests. Neither path
asserts AMD/NPU execution or clinical effectiveness.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import platform
import secrets
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.aipc_preflight import check
from scripts.demo_preflight import inspect
from scripts.evaluate_aipc import evaluate
from cardio import __version__


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def revision():
    info = ROOT/'RELEASE_INFO.json'
    if info.is_file():
        return json.loads(info.read_text(encoding='utf-8')).get('source_commit')
    try:
        return subprocess.run(['git','rev-parse','HEAD'], cwd=ROOT, check=True,
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def image_flow():
    from cardio.app import create_app
    trace = []
    with tempfile.TemporaryDirectory(prefix='cardio-accept-') as temp:
        app = create_app(temp, synchronous=True)
        client = app.test_client()
        def call(method, path, expected, body=None, headers=None):
            reply = client.open(path, method=method, json=body, headers=headers)
            trace.append({'method':method,'path':path,'status':reply.status_code})
            if reply.status_code != expected:
                raise ValueError(f'{method} {path}: expected {expected}, got {reply.status_code}')
            return reply
        try:
            token = call('GET','/api/session',200).json['csrf']
            call('POST','/api/auth/setup',200,{'username':'acceptance-local','password':secrets.token_urlsafe(32)}, {'X-CSRF-Token':token})
            headers = {'X-CSRF-Token':call('GET','/api/session',200).json['csrf']}
            phantom = call('POST','/api/analysis/phantom',200,{},headers).json['case_id']
            entries = [('phantom',{'case_id':phantom,'mode':'demo','center':16,'threshold':120,'use_llm':False})]
            for value in (0,2):
                entries.append((f'mri_class_{value}',{'case_id':'research-mri-01','mode':'inspect','center':4,
                                'segmentation_mode':'provided_reference','reference_class':value,'use_llm':False}))
            results=[]
            for name, body in entries:
                start=time.perf_counter()
                result=call('POST','/api/analyses',201,body,headers).json
                rec=result.get('reconstruction') or {};stats=rec.get('statistics') or {}
                source=rec.get('mesh') or {};display=rec.get('display_mesh') or {}
                if not source.get('vertices') or not display.get('vertices'):
                    raise ValueError(f'{name}: required source or display surface is absent')
                if len(display['faces'])>180000:
                    raise ValueError(f'{name}: preview exceeds the display budget')
                if not all(result['integrity'].get(key) for key in ('source_current','geometry_current','mask_valid','audit_valid')):
                    raise ValueError(f'{name}: analysis integrity failed')
                mask=call('GET',f"/api/analyses/{result['id']}/slices/{body['center']}?view=mask",200)
                if mask.mimetype!='image/png':raise ValueError('Mask route did not return PNG')
                call('GET',f"/api/analyses/{result['id']}/mesh.obj",200)
                # Verify that report facts require the explicit review gate.
                call('GET',f"/api/analyses/{result['id']}/report-draft",400)
                if name=='phantom':
                    dice=result['comparison']['segmentation']['dice']
                    if stats['foreground_voxels']!=28736 or dice!=1.0:raise ValueError('Phantom consistency failed')
                else:
                    expected=75846 if body['reference_class']==0 else 25573
                    if stats['foreground_voxels']!=expected:raise ValueError('Source-label voxel count differs')
                    if stats['physical_volume_mm3'] is not None:raise ValueError('Unverified MRI geometry acquired physical units')
                if result['discussion']['recommendation']['procedure'] is not None:
                    raise ValueError('Unexpected automatic procedure decision')
                results.append({'case':name,'scope':'synthetic_fixture' if name=='phantom' else 'provided_research_label_reconstruction_not_inference',
                    'foreground_voxels':stats['foreground_voxels'],'physical_volume_mm3':stats['physical_volume_mm3'],
                    'source_vertices':len(source['vertices']),'source_triangles':len(source['faces']),
                    'preview_vertices':len(display['vertices']),'preview_triangles':len(display['faces']),
                    'mask_file_sha256':result['segmentation'].get('mask_sha256'),
                    'source_label_sha256':result['segmentation'].get('label_sha256'),
                    'source_slice_sha256':result['source'].get('slice_sha256'),
                    'units':source.get('units'),'elapsed_ms':round((time.perf_counter()-start)*1000,3),
                    'measurement_scope':'Flask test-client request, serial analysis, persistence and export; excludes startup, browser rendering and human work'})
            if not app.store.verify()['valid']:raise ValueError('Event chain failed')
            return {'passed':True,'cases':results,'route_calls':trace,'authentication_and_csrf_used':True,
                    'report_review_gate_verified':True,'browser_rendering_verified':False,'tcp_transport_measured':False}
        finally:
            app.pool.shutdown(wait=True)


def llm_passed(result):
    """An urgent bypass or zero records must never masquerade as LLM execution."""
    summary=result.get('summary',{})
    return (summary.get('attempted',0)>0 and summary.get('failed_outputs')==0
            and result.get('local_model_outputs',0)>0
            and summary.get('exact_full_turn_validation_passed')==summary.get('accepted_outputs'))


def render_report(report, output):
    esc=lambda v:html.escape(str(v))
    stages=report['checks']
    names={'preflight':'資料與套件檢查','baseline':'412 筆文字規則基線','images':'合成體模與研究標籤 3D','llm':'本機語言模型輸出'}
    rows=''.join(f'<tr><td>{names.get(k,k)}</td><td>{"通過" if v.get("passed") else "未執行" if v.get("skipped") else "受阻"}</td><td>{esc(v.get("note",v.get("error","")))}</td></tr>' for k,v in stages.items())
    images=stages.get('images',{}).get('result',{}).get('cases',[])
    mesh=''.join('<tr>'+''.join(f'<td>{esc(v)}</td>' for v in [r['case'],r['foreground_voxels'],r['source_triangles'],r['preview_triangles'],r['units'],r['physical_volume_mm3']])+'</tr>' for r in images)
    baseline=stages.get('baseline',{}).get('summary',{})
    llm=stages.get('llm',{})
    links=' · '.join(f'<a href="{name}">{label}</a>' for name,label in [
        ('acceptance.json','完整驗收 JSON'),('baseline.json','規則基線 JSON'),('images.json','影像驗收 JSON'),
        ('llm.json','本機模型 JSON'),('software.json','套件與環境 JSON'),('preflight.json','模型就緒 JSON')]
        if (output.parent/name).is_file())
    page=f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CardioAgent 本機驗收紀錄</title>
<style>body{{font:16px/1.65 system-ui,sans-serif;color:#182027;background:#fff;max-width:1050px;margin:40px auto;padding:0 24px}}h1{{font-size:27px}}h2{{font-size:20px;margin-top:32px}}table{{width:100%;border-collapse:collapse;margin:12px 0}}th,td{{border:1px solid #d4d9dd;padding:9px;text-align:left}}th{{background:#eef1f3}}.meta{{color:#53616b}}a{{color:#315a70}}code{{word-break:break-all}}@media print{{body{{margin:12mm}}}}</style>
<h1>CardioAgent {esc(report['software_version'])} 本機驗收紀錄</h1><p class="meta">{esc(report['created_at'])} · {esc(report['host']['os'])} · Python {esc(report['host']['python'])}</p>
<p>本次要求範圍：<strong>{'基線、影像與本機模型' if report['llm_requested'] else '基線與影像'}</strong>；結果：<strong>{'通過' if report['passed_requested_scope'] else '有受阻項目'}</strong>。</p>
<table><tr><th>驗收項目</th><th>結果</th><th>說明</th></tr>{rows}</table>
<h2>文字分析</h2><p>規則基線：{esc(baseline.get('attempted','未執行'))} 筆，分類 accuracy {esc(baseline.get('section_accuracy'))}，macro F1 {esc(baseline.get('macro_f1_supported_classes'))}。指標為研究資料的段落分類，不是診斷效能。</p>
<p>本機模型實際接納輸出：{esc(llm.get('local_model_outputs',0))} 筆；人工急性旗標規則跳過模型的結果另列。模型延遲和基線延遲各自保存在原始 JSON，未量測 TTFT 或 NPU 加速比。</p>
<h2>3D 與來源一致性</h2><table><tr><th>案例</th><th>原始體素</th><th>來源三角面</th><th>顯示三角面</th><th>來源單位</th><th>mm³</th></tr>{mesh}</table>
<p>MRI 為來源標籤的重建，不是模型預測；來源網格與顯示用平滑表面分開。null 表示空間校正不足，不能量測物理體積。影像流程經過帳號、CSRF 與人工覆核關卡；本次未驗證瀏覽器繪圖。</p>
<h2>證據邊界</h2><p>CPU／GPU／NPU 驅動或模型 recipe 只代表配置。此報告不自動宣告 AMD 或 NPU 執行成功；仍須目標機上同一模型請求的 runtime 裝置紀錄。尚未量測臨床效益、手術規劃準確度或真實醫院介接。</p>
<p>來源版本：<code>{esc(report.get('source_commit'))}</code>。所有原始 JSON 可由同資料夾回查；重新執行建立新資料夾，避免覆寫紀錄。</p><p>{links}</p></html>'''
    output.write_text(page,encoding='utf-8')


def run(output, use_llm=False, model='Phi-4-mini-instruct-GGUF', llm_limit=12):
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    report={'schema':'cardio-aipc-acceptance/1','created_at':datetime.now(timezone.utc).isoformat(),
        'software_version':__version__,'source_commit':revision(),
        'host':{'os':platform.system(),'architecture':platform.machine(),'python':platform.python_version()},
        'llm_requested':use_llm,'selected_model':model,'checks':{},'amd_hardware_execution_verified':False,
        'npu_execution_verified':False,'clinical_effectiveness_measured':False,'ttft_measured':False,
        'peak_memory_measured':False,'browser_rendering_verified':False}
    checks=report['checks']
    try:
        preflight=check(False,model);software=inspect(probe_service=False)
        write_json(output/'preflight.json',preflight);write_json(output/'software.json',software)
        checks['preflight']={'passed':bool(preflight['baseline_ready'] and software['workflow_demo_ready']),
                             'note':'本機模型是否可用另列；套件及來源檢查不宣稱硬體推論。'}
    except Exception as exc:
        preflight={};checks['preflight']={'passed':False,'error':str(exc)[:400]}
    for key,operation in [('baseline',lambda:evaluate('baseline')),('images',image_flow)]:
        try:
            result=operation();write_json(output/(key+'.json'),result)
            if key=='baseline':
                summary=result['summary']
                checks[key]={'passed':summary['attempted']==412 and summary['failed_outputs']==0,
                             'summary':summary,'actual_engine_counts':result['actual_engine_counts']}
            else:checks[key]={'passed':result['passed'],'result':result}
        except Exception as exc:checks[key]={'passed':False,'error':str(exc)[:400]}
    if not use_llm:
        checks['llm']={'passed':False,'skipped':True,'local_model_outputs':0,'note':'未要求執行模型；以 --llm --model 明確啟用。'}
    elif not preflight.get('local_llm_ready'):
        checks['llm']={'passed':False,'local_model_outputs':0,'note':'選定模型尚未可用，未以規則輸出代替模型。'}
    else:
        try:
            result=evaluate('llm',model,'synthetic_demo',llm_limit);write_json(output/'llm.json',result)
            checks['llm']={'passed':llm_passed(result),'summary':result['summary'],
                           'local_model_outputs':result['local_model_outputs'],'actual_engine_counts':result['actual_engine_counts']}
        except Exception as exc:checks['llm']={'passed':False,'error':str(exc)[:400],'local_model_outputs':0}
    report['passed_requested_scope']=all(checks[k]['passed'] for k in ('preflight','baseline','images')) and (not use_llm or checks['llm']['passed'])
    write_json(output/'acceptance.json',report)
    render_report(report,output/'index.html')
    paths=sorted(p for p in output.iterdir() if p.is_file())
    (output/'SHA256SUMS.txt').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in paths),encoding='utf-8')
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--llm',action='store_true',help='Execute the selected local model on synthetic cases; failures fail this acceptance scope.')
    parser.add_argument('--model',default=None)
    parser.add_argument('--llm-limit',type=int,default=12,help='1..12 synthetic cases; acute cases may explicitly bypass the model.')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    if not 1<=args.llm_limit<=12:parser.error('--llm-limit must be in 1..12')
    config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    model=args.model or config.get('planner_model') or 'Phi-4-mini-instruct-GGUF'
    output=args.output or ROOT/'evidence/target_runs'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6])
    if output.exists():parser.error('Output already exists. Select a new directory to preserve previous evidence.')
    result=run(output,args.llm,model,args.llm_limit)
    print(json.dumps({'report':str(output/'index.html'),'passed_requested_scope':result['passed_requested_scope'],
                     'checks':{k:v['passed'] for k,v in result['checks'].items()},'npu_execution_verified':False},ensure_ascii=False,indent=2))
    return 0 if result['passed_requested_scope'] else 1


if __name__=='__main__':raise SystemExit(main())
