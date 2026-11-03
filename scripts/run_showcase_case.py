"""Execute the included research MRI case and export inspectable showcase evidence.

Uses real Flask routes in a new isolated data directory; no model mocks and no
clinical signatory. The final state is a scripted research demonstration only.
"""
import argparse
import copy
import json
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cardio.app import create_app
from cardio import __version__


def run(data, output):
    if (data / 'cardio.sqlite3').exists():
        raise SystemExit('Use a new data directory; existing records are never overwritten.')
    output.mkdir(parents=True, exist_ok=True)
    app = create_app(data, synchronous=True, access_control=False, anonymous_actor='showcase-demo')
    app.hospital.signature_kind = 'scripted research demo confirmation; no authenticated clinician or hospital signature'
    client = app.test_client()
    headers = {'X-CSRF-Token': client.get('/api/session').json['csrf']}
    requests = []

    def call(method, path, body=None, expected=200):
        start = time.perf_counter()
        response = client.open(path, method=method, json=body, headers=headers)
        requests.append({'method': method, 'path': path, 'status': response.status_code,
                         'elapsed_ms': round((time.perf_counter()-start)*1000, 3)})
        if response.status_code != expected:
            raise AssertionError((path, response.status_code, response.get_json(silent=True)))
        return response

    def save(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                            separators=(',', ':'))+'\n', encoding='utf-8')

    try:
        body = {'case_id': 'research-mri-01',
                'goal': 'DEMO-MRI-001｜心臟 MRI 會診前影像與證據準備（研究展示）',
                'question': '核對 8 張研究 MRI、提供標籤與 3D 表面的來源，完成研究報告與交接。'
                            '急性狀態與背景欄位僅演練研究情境，不代表真實病史或臨床評估。',
                'context': {'acute': False, 'history_complete': None, 'consent': True},
                'settings': {'mode': 'inspect', 'center': 4, 'threshold': 128,
                             'segmentation_mode': 'provided_reference', 'reference_class': 0,
                             'use_llm': False, 'model': ''}}
        incomplete = call('POST', '/api/workspace/orders', body, 201).json
        blocked = call('POST', f"/api/workspace/orders/{incomplete['id']}/execute",
                       {'version': incomplete['version']}, 400).json
        assert not app.preparation.get(incomplete['id'])['analysis_id']
        body['context']['history_complete'] = True
        order = call('POST', '/api/workspace/orders', body, 201).json
        started = call('POST', f"/api/workspace/orders/{order['id']}/execute",
                       {'version': order['version']}, 202).json
        assert started['status'] == 'pending_review', started
        ident = started['analysis_id']
        result = call('GET', f'/api/analyses/{ident}').json
        assert result['source']['shape_zyx'] == [8, 512, 428]
        assert result['reconstruction']['statistics']['foreground_voxels'] == 75846
        assert result['reconstruction']['statistics']['physical_volume_mm3'] is None
        assert result['comparison']['segmentation']['dice'] is None
        assert result['recognition']['inference_executed'] is False
        assert not result['review']
        review_reason = '研究展示流程確認：核對來源、標籤與 voxel 單位。非醫師診斷、非臨床簽核，未確認手術適應症。'
        reviewed = call('POST', f'/api/analyses/{ident}/review',
                        {'version': result['version'], 'decision': 'approve',
                         'review_confirmed': True, 'reason': review_reason}).json
        assert reviewed['status'] == 'approved'
        draft = call('GET', f'/api/analyses/{ident}/report-draft').json
        studies = call('GET', '/api/hospital/studies').json
        study = next(s for s in studies if s['case_id'] == body['case_id'])
        sid = study['id']
        fields = copy.deepcopy(draft['fields'])
        fields['indication'] = '研究展示：整理影像來源、提供標籤與三維表面，完成可追溯的會診準備交接。未提供個人臨床適應症。'
        fields['comparison'] = '未提供歷次檢查或獨立預測遮罩。提供標籤用於重建，未對同一標籤回算 Dice。'
        fields['impression'] = '研究影像與提供標籤已完成來源對齊檢核及三維表面輸出。未執行病灶辨識，未形成臨床診斷。此為示範流程確認，非醫師簽核。'
        fields['limitations'] += ' 未驗證空間間距、方向及類別解剖名稱。臨床背景、功能、風險及病人偏好仍待專業團隊補齊。'
        saved = call('POST', f'/api/hospital/studies/{sid}/report',
                     {'version': 0, 'status': 'draft', 'fields': fields}).json
        preliminary = call('POST', f'/api/hospital/studies/{sid}/report',
                           {'version': saved['version'], 'status': 'preliminary', 'fields': fields}).json
        final = call('POST', f'/api/hospital/studies/{sid}/report/sign',
                     {'version': preliminary['version'], 'review_confirmed': True}).json
        assert final['status'] == 'final' and final['version'] == 3
        immutable = call('POST', f'/api/hospital/studies/{sid}/report',
                          {'version': 3, 'status': 'draft', 'fields': fields}, 409).json
        fhir = call('GET', f'/api/hospital/studies/{sid}/fhir').json
        assert len(fhir['entry']) == 3
        assert next(e['resource'] for e in fhir['entry'] if e['resource']['resourceType']=='DiagnosticReport')['status']=='final'
        for i in range(8):
            for view in ('original', 'overlay', 'mask'):
                response = call('GET', f'/api/analyses/{ident}/slices/{i}?view={view}')
                (output/f'slice-{i+1:02}-{view}.png').write_bytes(response.data)
        for kind in ('obj', 'stl'):
            (output/f'source-surface-voxel.{kind}').write_bytes(call('GET', f'/api/analyses/{ident}/mesh.{kind}').data)
        (output/'segmentation-mask.npy').write_bytes((app.analysis.folder/(ident+'.mask.npy')).read_bytes())
        history = call('GET', f'/api/hospital/studies/{sid}/history').json
        assert [r['status'] for r in history['revisions']] == ['draft', 'preliminary', 'final']
        export = call('GET', f"/api/workspace/orders/{order['id']}/export").json
        save('preparation-export.json', export)
        save('blocked-order.json', {'order': incomplete, 'attempt': blocked, 'no_analysis_created': True})
        save('final-report.json', final)
        save('report-history.json', history)
        save('handoff-fhir.json', fhir)
        save('report-draft.json', draft)
        save('requests.json', requests)
        audit = app.store.verify()
        assert audit['valid']
        result = export['analysis']['analysis']
        mesh = result['reconstruction']['mesh']
        display = result['reconstruction']['display_mesh']
        save('source-mesh.json', mesh)
        # Full evidence remains in the ZIP; small summary drives the read-only exhibit.
        summary = {'schema': 'cardio-completed-showcase/1', 'case_label': 'DEMO-MRI-001',
                   'version': __version__, 'source_commit': '655deedf7caae717e505855bd8065f0a23eb8efe',
                   'executed_at': datetime.now(timezone.utc).isoformat(),
                   'execution': {'kind': 'real_flask_routes_isolated_CPython_run', 'python': platform.python_version(),
                                 'os': platform.system(), 'amd_hardware_verified': False,
                                 'llm_executed': False, 'trained_image_model_executed': False,
                                 'machine_elapsed_ms': started['machine_elapsed_ms']},
                   'order_id': order['id'], 'analysis_id': ident, 'study_id': sid,
                   'spec_sha256': order['spec_sha256'], 'source': result['source'],
                   'statistics': result['reconstruction']['statistics'],
                   'source_mesh': {'vertices': len(mesh['vertices']), 'triangles': len(mesh['faces']), 'units': mesh['units']},
                   'display_mesh': {'vertices': len(display['vertices']), 'triangles': len(display['faces']),
                                    'display_scale_xyz': display.get('display_scale_xyz')},
                   'stages': result['stages'], 'review': result['review'], 'discussion': result['discussion'],
                   'report': final, 'report_versions': [{'version': r['version'], 'status': r['status'], 'at': r['updated_at']} for r in history['revisions']],
                   'audit': audit, 'checks': {'missing_context_blocked': True, 'source_integrity': export['analysis']['current_source_integrity'],
                                           'final_report_immutable': True, 'fhir_resources': 3, 'requests': len(requests)},
                   'notice': '實際執行的研究展示紀錄；覆核與 final 狀態為腳本示範確認，未驗證醫事身分或建立臨床診斷。'}
        save('case-summary.json', summary)
        with app.store.connect() as db:
            events = [dict(row) | {'data': json.loads(row['data'])} for row in db.execute('SELECT * FROM events ORDER BY seq')]
        save('audit-events.json', {'events': events, 'verification': audit})
        print(json.dumps({'output': str(output), 'analysis_id': ident, 'report_status': final['status'],
                          'report_version': final['version'], 'voxels': 75846, 'machine_elapsed_ms': started['machine_elapsed_ms'],
                          'audit_valid': audit['valid'], 'requests': len(requests)}, ensure_ascii=False))
    finally:
        app.pool.shutdown(wait=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.data_dir.resolve(), args.output.resolve())
