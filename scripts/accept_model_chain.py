"""Execute one isolated CAC/local-LLM tool chain and retain source-bound evidence.

No model downloads, clinical approval, report signing or hardware certification.
Input PNG order and authorization are supplied explicitly by the operator.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import html
import io
import json
from pathlib import Path
import platform
import re
import secrets
import sys
import tempfile
import time
import uuid
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cardio import __version__
from cardio.agent import validate_context
from cardio.app import create_app
from cardio.imaging import sha256
from cardio.planner import TOOLS
from cardio.store import canonical
from scripts.accept_aipc import revision, write_json


def assess(run, model, expected_sha256, expected_manifest_sha256, audit, source_current):
    """Strict acceptance; workflow fallback can never count as model acceptance."""
    logs = run.get('tool_log', [])
    plans = [entry.get('plan', {}) for entry in logs]
    ids = [p.get('response_id') for p in plans]
    vision = run.get('vision', {})
    def usage_valid(p):
        usage = p.get('usage')
        return (isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0
                for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
                and usage['completion_tokens'] > 0
                and usage['total_tokens'] == usage['prompt_tokens'] + usage['completion_tokens'])
    checks = {
        'six_tools_completed': run.get('completed') == TOOLS and [e.get('tool') for e in logs] == TOOLS,
        'pending_human_review': run.get('status') == 'pending_review' and not run.get('review'),
        'six_local_model_responses': len(plans) == 6 and all(p.get('source') == 'lemonade' for p in plans),
        'matching_model_identity': len(plans) == 6 and all(p.get('model') == model and p.get('requested_model') == model for p in plans),
        'six_distinct_response_ids': len(ids) == 6 and all(isinstance(i, str) and i.strip() for i in ids) and len(set(ids)) == 6,
        'token_usage_recorded': len(plans) == 6 and all(usage_valid(p) for p in plans),
        'onnx_inference_executed': vision.get('inference_executed') is True,
        'expected_vision_weights': vision.get('model_sha256') == expected_sha256,
        'expected_vision_manifest': vision.get('model_manifest_sha256') == expected_manifest_sha256,
        'trained_model_declared': vision.get('trained') is True and bool(vision.get('model_provenance')),
        'postprocessing_recorded': all(k in vision.get('postprocessing', {}) for k in ('confidence', 'nms_iou')),
        'source_current': source_current is True,
        'audit_valid': audit.get('valid') is True,
    }
    return {'checks': checks, 'passed': all(checks.values()),
            'local_model_responses': sum(p.get('source') == 'lemonade' for p in plans),
            'fallback_responses': sum(p.get('source') == 'rules_fallback' for p in plans),
            'response_ids': ids}


def render(report, output):
    esc = lambda value: html.escape(str(value))
    names = {'input': '授權輸入與背景', 'vision_model': '指定權重與訓練來源聲明',
             'local_model': '已下載的本機模型', 'chain': '同一病例的六工具執行鏈'}
    rows = ''.join(f'<tr><td>{names.get(k,k)}</td><td>{"通過" if v.get("passed") else "受阻"}</td><td>{esc(v.get("error", ""))}</td></tr>'
                   for k, v in report['checks'].items())
    detail = report['checks'].get('chain', {}).get('result', {})
    checks = ''.join(f'<li>{esc(k)}：{"通過" if v else "未通過"}</li>' for k,v in detail.get('checks', {}).items())
    links = ' · '.join(f'<a href="{p.name}">{p.name}</a>' for p in sorted(output.glob('*.json')))
    page = f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CardioAgent CAC 與本機模型同次驗收</title><style>body{{font:16px/1.65 system-ui;color:#20282d;max-width:1000px;margin:32px auto;padding:0 24px}}table{{width:100%;border-collapse:collapse}}td,th{{border:1px solid #ccd2d6;padding:10px;text-align:left}}th{{background:#f1f3f4}}code{{overflow-wrap:anywhere}}a{{color:#315a70}}</style>
<h1>CAC 與本機模型同次驗收 · {esc(report['software_version'])}</h1>
<p>{esc(report['created_at'])} · {esc(report['host']['os'])} / Python {esc(report['host']['python'])}</p>
<p>本次軟體鏈：<strong>{'通過' if report['passed_requested_scope'] else '受阻'}</strong>；輸入：{esc(report['input_scope'])}。</p>
<table><tr><th>項目</th><th>狀態</th><th>原因</th></tr>{rows}</table><ul>{checks}</ul>
<p>這次保持待人工覆核，沒有自動建立會診任務或簽署報告。訓練模型依登錄聲明及指定 SHA256 核對，未驗證原 PyTorch 的等價性、病灶準確度或臨床效益。</p>
<p>合成輸入只驗證軟體串接。Provider、模型目錄及 recipe 不證明 AMD／NPU 裝置實際執行；需另附同一 response ID 的 runtime 裝置紀錄。</p>
<p>來源版本：<code>{esc(report['source_commit'])}</code>。來源程式雜湊與本次原始資料見下列 JSON。</p><p>{links} · <a href="SHA256SUMS.txt">SHA256SUMS.txt</a></p></html>'''
    (output/'index.html').write_text(page, encoding='utf-8')


def run(output, model, expected_vision_sha256, images=None, context=None, authorized=False, synthetic=False, center=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema': 'cardio-model-chain-acceptance/1', 'id': uuid.uuid4().hex,
        'created_at': datetime.now(timezone.utc).isoformat(), 'software_version': __version__,
        'source_commit': revision(), 'host': {'os': platform.system(), 'architecture': platform.machine(), 'python': platform.python_version()},
        'selected_model': model, 'expected_vision_sha256': expected_vision_sha256,
        'input_scope': 'synthetic_software_fixture' if synthetic else 'operator_authorized_ordered_ct_png',
        'checks': {}, 'passed_requested_scope': False, 'research_input_chain_verified': False,
        'amd_hardware_execution_verified': False, 'npu_execution_verified': False,
        'clinical_accuracy_verified': False, 'pytorch_onnx_equivalence_verified': False,
        'human_review_performed': False, 'raw_images_exported': False,
        'measurement_scope': 'isolated Flask test client and real loopback LLM; excludes browser, human work, TTFT and device profiling',
        'source_files_sha256': {name: sha256(ROOT/name) for name in
            ('cardio/agent.py', 'cardio/planner.py', 'cardio/imaging.py', 'scripts/accept_model_chain.py', 'config.json')}}
    app = None
    try:
        if not isinstance(model, str) or not model.strip():
            raise ValueError('請指定已下載的本機文字模型 ID。')
        if not isinstance(expected_vision_sha256, str) or not re.fullmatch('[a-f0-9]{64}', expected_vision_sha256):
            raise ValueError('請提供預期影像權重的完整小寫 SHA256。')
        if synthetic:
            if images or context is not None:
                raise ValueError('合成測試不可混用真實影像或背景。')
            context = {'age': 62, 'acute': False, 'history_complete': True, 'consent': True}
        else:
            context = validate_context(context)
            if not authorized or not images or not 3 <= len(images) <= 64:
                raise ValueError('請確認資料授權並依實際順序提供 3–64 張 CT PNG。')
            if context['acute'] is not False or context['age'] is None or context['history_complete'] is not True or context['consent'] is not True:
                raise ValueError('背景未齊或有急性警訊；不執行一般模型鏈。')
        checks = report['checks']
        # A separate data directory is always used. Accounts and patient data in
        # the regular workstation are never read or changed by this command.
        with tempfile.TemporaryDirectory(prefix='cardio-model-chain-') as temp:
            app = create_app(temp, synchronous=True)
            app.agent.planner.config = {**app.agent.planner.config, 'planner_model': model}
            client = app.test_client()
            trace = []
            def call(method, path, expected=200, body=None, data=None, headers=None):
                response = client.open(path, method=method, json=body, data=data, headers=headers)
                trace.append({'method': method, 'path': path, 'status': response.status_code})
                if response.status_code != expected:
                    raise ValueError(f'{method} {path}: {response.status_code} {response.get_json(silent=True)}')
                return response
            token = call('GET', '/api/session').json['csrf']
            call('POST', '/api/auth/setup', body={'username': 'chain-acceptance', 'password': secrets.token_urlsafe(32)}, headers={'X-CSRF-Token':token})
            headers = {'X-CSRF-Token':call('GET', '/api/session').json['csrf']}
            if synthetic:
                case_id = 'demo-ct-positive'
            else:
                files, raw_hashes, total = [], [], 0
                for i, path in enumerate(images):
                    path = Path(path)
                    if path.suffix.lower() != '.png' or path.stat().st_size > 8*1024*1024:
                        raise ValueError('模型鏈輸入只接受每張至多 8 MiB 的訓練窗位 PNG。')
                    raw = path.read_bytes();total += len(raw)
                    if total > 40*1024*1024:
                        raise ValueError('模型鏈 PNG 輸入總大小上限 40 MiB。')
                    with Image.open(io.BytesIO(raw)) as image:
                        if image.format != 'PNG' or image.mode != 'L' or image.size != (512,512):
                            raise ValueError('模型鏈需要真正 512×512 的 8-bit 灰階 PNG，禁止隱式縮放或色彩轉換。')
                    files.append((io.BytesIO(raw), f'{i:04}.png'))
                    raw_hashes.append(hashlib.sha256(raw).hexdigest())
                case_id = call('POST', '/api/cases', 201,
                    data={'modality':'CT', 'order_confirmed':'true', 'images':files}, headers=headers).json['id']
                report['ordered_upload_sha256'] = raw_hashes
            case = app.store.case(case_id)
            selected = len(case['slices'])//2 if center is None else center
            if type(selected) is not int or not 0 <= selected < len(case['slices']):
                raise ValueError('中心切片索引無效。')
            checks['input'] = {'passed':True, 'case_id':case_id, 'center':selected, 'slices':len(case['slices'])}
            try:
                manifest, _ = app.detector.manifest()
                write_json(output/'vision-manifest.json', manifest)
                if manifest['sha256'] != expected_vision_sha256:
                    raise ValueError('已登錄權重與預期 SHA256 不同。')
                if manifest.get('trained') is not True or not isinstance(manifest.get('provenance'), str) or not manifest['provenance'].strip():
                    raise ValueError('未提供已訓練模型聲明與來源；測試 ONNX 不算 CAC 部署驗收。')
                checks['vision_model'] = {'passed':True, 'declaration_only':True}
            except Exception as exc:
                checks['vision_model'] = {'passed':False, 'error':str(exc)[:500]}
            try:
                selected_model = app.agent.planner.local_model(model)
                write_json(output/'local-model.json', {k:selected_model.get(k) for k in ('id','recipe','downloaded')})
                checks['local_model'] = {'passed':True, 'inference_not_yet_executed':True}
            except Exception as exc:
                checks['local_model'] = {'passed':False, 'error':str(exc)[:500]}
            if checks['vision_model']['passed'] and checks['local_model']['passed']:
                start = time.perf_counter()
                ident = call('POST', '/api/runs', 202, body={'case_id':case_id, 'mode':'model', 'planner':'lemonade',
                    'center':selected, 'context':context}, headers=headers).json['id']
                evidence = call('GET', f'/api/runs/{ident}/export').json
                write_json(output/'run-evidence.json', evidence)
                current = app.store.run(ident)
                try:
                    app.agent.verify_source(current);source_current = True
                except (ValueError, OSError, KeyError):
                    source_current = False
                expected_manifest = hashlib.sha256(canonical(manifest).encode()).hexdigest()
                result = assess(current, model, expected_vision_sha256, expected_manifest, app.store.verify(), source_current)
                try:
                    final_manifest, _ = app.detector.manifest()
                    result['checks']['vision_model_unchanged'] = canonical(final_manifest) == canonical(manifest)
                except (ValueError, OSError, KeyError):
                    result['checks']['vision_model_unchanged'] = False
                result['passed'] = all(result['checks'].values())
                result['no_tasks_created'] = app.store.tasks(ident) == []
                checks['chain'] = {'passed':result['passed'] and result['no_tasks_created'], 'result':result,
                    'run_id':ident, 'status':current['status'], 'error':current.get('error',''),
                    'elapsed_ms':round((time.perf_counter()-start)*1000,3)}
                with app.store.connect() as db:
                    events = [dict(r) for r in db.execute('SELECT * FROM events ORDER BY seq')]
                for entry in events:
                    entry['data'] = json.loads(entry['data'])
                write_json(output/'audit-events.json', events)
            else:
                checks['chain'] = {'passed':False, 'error':'必要模型受阻，未啟動工具或以合成／規則結果替代。'}
            write_json(output/'requests.json', trace)
            report['passed_requested_scope'] = all(v['passed'] for v in checks.values())
            report['research_input_chain_verified'] = report['passed_requested_scope'] and not synthetic
            # Shut down before removing Windows-backed temporary files.
            app.pool.shutdown(wait=True);app = None
    except Exception as exc:
        report['checks']['input_or_execution'] = {'passed':False, 'error':str(exc)[:500]}
    finally:
        if app is not None:
            app.pool.shutdown(wait=True)
        write_json(output/'acceptance.json', report)
        render(report, output)
        paths = sorted(p for p in output.iterdir() if p.is_file())
        (output/'SHA256SUMS.txt').write_text(''.join(f'{sha256(p)}  {p.name}\n' for p in paths), encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument('--images', nargs='+', type=Path, help='Ordered CT PNGs, using the training window; never lexical-sorted by this script')
    sources.add_argument('--synthetic', action='store_true', help='Explicitly synthetic software-chain rehearsal, never research/clinical evidence')
    parser.add_argument('--context', type=Path, help='JSON: age, acute, history_complete, consent')
    parser.add_argument('--authorized-input', action='store_true')
    parser.add_argument('--model', required=True)
    parser.add_argument('--expected-vision-sha256', required=True)
    parser.add_argument('--center', type=int, help='Zero-based center; default is middle slice')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.images and (not args.context or not args.authorized_input):
        parser.error('--images requires --context and --authorized-input')
    if args.synthetic and (args.context or args.authorized_input):
        parser.error('--synthetic cannot use real context or authorization flags')
    try:
        context = json.loads(args.context.read_text(encoding='utf-8-sig')) if args.context else None
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    output = args.output or ROOT/'evidence/target_runs'/('chain-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6])
    if output.exists():
        parser.error('Output exists; use a new directory to preserve previous evidence.')
    report = run(output, args.model, args.expected_vision_sha256, args.images, context,
                 args.authorized_input, args.synthetic, args.center)
    print(json.dumps({'report':str(output/'index.html'), 'passed_requested_scope':report['passed_requested_scope'],
                      'research_input_chain_verified':report['research_input_chain_verified'],
                      'npu_execution_verified':False}, ensure_ascii=False, indent=2))
    return 0 if report['passed_requested_scope'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
