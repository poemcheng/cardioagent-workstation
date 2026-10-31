"""Authenticated analysis, explanation, geometry and human-review routes."""
import io
import json
import math
import threading
import time
import secrets
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from flask import jsonify, request, session, render_template, redirect, Response

from .analysis import Analysis
from .discussion import Discussion
from .imaging import display_pixels
from .volume import generate_phantom, mesh_to_obj, mesh_to_stl


def register_analysis_routes(app, root, data, store, detector, planner, hospital, current_user, actions):
    analysis = Analysis(root, data, store, detector, planner)
    discussion = Discussion(root)
    execution = threading.Lock()
    app.analysis, app.discussion = analysis, discussion

    def present(result):
        return result | {"integrity": analysis.integrity(result)}

    @app.get('/analysis')
    def analysis_workspace():
        if not current_user():
            return redirect('/?next=analysis')
        session.setdefault('csrf', secrets.token_urlsafe(32))
        return render_template('analysis.html', csrf=session['csrf'])

    @app.get('/api/analysis/status')
    def analysis_status():
        segmentation = {'configured': False}
        try:
            from .segmentation import Segmenter
            manifest, _ = Segmenter(root, planner.config).manifest()
            segmentation.update(configured=True, name=manifest['name'], sha256=manifest['sha256'],
                                modality=manifest['modality'], anatomical_label=manifest['class_labels'][manifest['target_class']])
        except Exception as exc:
            segmentation['reason'] = str(exc)[:300]
        return jsonify(segmentation=segmentation, reconstruction='voxel_boundary_surface',
                       geometry_validation='uniform_dicom_headers_or_explicit_synthetic_geometry',
                       clinical_accuracy_verified=False, amd_hardware_verified=False, npu_execution_verified=False)

    @app.post('/api/analysis/phantom')
    def analysis_phantom():
        # Only explicit loading adds the fourth study; existing demo cases stay unchanged.
        with actions:
            try:
                case = store.case('demo-volume-phantom')
            except KeyError:
                case = generate_phantom(data)
                store.put_case(case)
            existing = next((s for s in hospital.list() if s['case_id'] == case['id']), None)
            if existing is None:
                existing = hospital.register({'case_id': case['id'], 'patient_id': 'PHANTOM-3D',
                    'patient_name': '3D 合成體模', 'accession': 'PHANTOM-3D-001',
                    'department': '幾何流程驗證', 'referrer': '本機解析體模',
                    'description': case['label'], 'clinical_question': '合成殼層的分割、比對及三維幾何驗證；非病人影像。'}, current_user()['username'])
            return jsonify(study=existing, case_id=case['id'], notice=case['source_detail'])

    @app.route('/api/analyses', methods=['GET', 'POST'])
    def analyses():
        if request.method == 'GET':
            return jsonify(analysis.list(request.args.get('case_id')))
        body = request.get_json()
        if not isinstance(body, dict):
            raise ValueError('分析輸入須為 JSON 物件。')
        urgent = False
        if isinstance(body.get('run_id'), str) and len(body['run_id']) <= 100:
            linked = store.run(body['run_id'])
            urgent = linked['case_id'] == body.get('case_id') and linked.get('context', {}).get('acute') is True
        acquired = False
        if not urgent:
            acquired = execution.acquire(blocking=False)
            if not acquired:
                return jsonify(error='目前已有完整分析執行中，請稍後再試。'), 409
        try:
            result = analysis.execute(body, current_user()['username'])
            start = time.perf_counter()
            context = {'acute': (result.get('run_snapshot') or {}).get('acute')}
            checklist = discussion.assess(result, context)
            result = analysis.attach_discussion(result['id'], checklist, current_user()['username'], result['version'])
            return jsonify(present(result)), 201
        finally:
            if acquired:
                execution.release()

    @app.get('/api/analyses/<ident>')
    def analysis_detail(ident):
        result = analysis.get(ident)
        store.record_event(ident, 'analysis_opened', {'actor': current_user()['username']})
        return jsonify(present(result))

    @app.post('/api/analyses/<ident>/discussion')
    def analysis_discussion(ident):
        body = request.get_json()
        if not isinstance(body, dict) or set(body) != {'version', 'context'}:
            raise ValueError('請提供目前分析版本與術前討論欄位。')
        result = analysis.get(ident)
        checklist = discussion.assess(result, body['context'])
        return jsonify(present(analysis.attach_discussion(ident, checklist, current_user()['username'], body['version'])))

    @app.post('/api/analyses/<ident>/review')
    def analysis_review(ident):
        return jsonify(present(analysis.review(ident, request.get_json(), current_user()['username'])))

    @app.get('/api/analyses/<ident>/export')
    def analysis_export(ident):
        payload = analysis.export(ident)
        store.record_event(ident, 'analysis_exported', {'actor': current_user()['username']})
        return Response(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
            mimetype='application/json', headers={'Content-Disposition': f'attachment; filename="analysis-{ident[:8]}.json"'})

    @app.get('/api/analyses/<ident>/mesh.<kind>')
    def analysis_mesh(ident, kind):
        result = analysis.get(ident)
        if kind not in {'obj', 'stl'}:
            raise ValueError('只支援 OBJ 或 STL。')
        mesh = (result.get('reconstruction') or {}).get('mesh')
        if not mesh or not mesh['vertices']:
            raise ValueError('本次分析沒有可匯出的表面網格。')
        content = mesh_to_obj(mesh) if kind == 'obj' else mesh_to_stl(mesh)
        store.record_event(ident, 'analysis_mesh_exported', {'actor': current_user()['username'], 'format': kind,
                           'units': mesh['units'], 'clinical_use_validated': False})
        return Response(content, mimetype='text/plain', headers={
            'Content-Disposition': f'attachment; filename="research-surface-{ident[:8]}-{mesh["units"]}.{kind}"'})

    @app.get('/api/analyses/<ident>/slices/<int:index>')
    def analysis_slice(ident, index):
        result = analysis.get(ident)
        case = store.case(result['case_id'])
        if not 0 <= index < len(case['slices']):
            raise ValueError('切片索引無效。')
        path = (Path(data) / case['slices'][index]).resolve()
        if not path.is_relative_to(Path(data).resolve()):
            raise ValueError('影像來源路徑無效。')
        from .imaging import sha256
        hashes = result['source'].get('slice_sha256', [])
        if len(hashes) != len(case['slices']) or sha256(path) != hashes[index]:
            raise ValueError('影像來源已變更，請重新分析後檢視。')
        arr = np.load(path, allow_pickle=False)
        view = request.args.get('view', 'original')
        if view not in {'original', 'overlay', 'mask'}:
            raise ValueError('影像顯示模式無效。')
        pixels = display_pixels(arr, is_hu=case.get('is_hu', False))
        image = Image.fromarray(pixels).convert('RGB')
        if view == 'mask' and not result.get('segmentation'):
            raise ValueError('本次分割受阻，沒有可顯示的遮罩。')
        if view in {'mask', 'overlay'} and result.get('segmentation'):
            mask = analysis.mask(ident)[index]
            if mask.shape != arr.shape:
                raise ValueError('分割遮罩與來源影像尺寸不一致。')
            if view == 'mask':
                image = Image.fromarray(mask.astype(np.uint8) * 255).convert('RGB')
            else:
                rgb = np.asarray(image).copy()
                rgb[mask] = (.60 * rgb[mask] + .40 * np.asarray([30, 190, 145])).astype(np.uint8)
                image = Image.fromarray(rgb)
        if view == 'overlay' and result['settings']['center'] == index:
            draw = ImageDraw.Draw(image)
            for number, candidate in enumerate((result.get('recognition') or {}).get('candidates', []), 1):
                box = candidate['xyxy']
                draw.rectangle(box, outline=(255, 201, 65), width=2)
                draw.text((box[0], max(0, box[1]-12)), str(number), fill=(255, 201, 65))
        out = io.BytesIO()
        image.save(out, format='PNG')
        return Response(out.getvalue(), mimetype='image/png')

    @app.get('/api/analyses/<ident>/report-draft')
    def analysis_report_draft(ident):
        result = analysis.get(ident)
        if result['status'] != 'approved':
            raise ValueError('請先完成人工分析覆核，再將事實草稿帶入報告。')
        analysis.assert_transferable(result)
        source, rec = result['source'], result.get('recognition')
        reconstruction = result.get('reconstruction')
        stats = (reconstruction or {}).get('statistics', {})
        count = len((rec or {}).get('candidates', []))
        findings = f"分析 {ident}；中心切片 {result['settings']['center']+1}。"
        findings += ('候選辨識受阻，沒有病灶判讀結果。' if rec is None else
                     '本次僅執行影像量測，未執行病灶辨識。' if not rec.get('inference_executed') and result['settings']['mode'] == 'inspect' else
                     f"候選 {count} 個；來源：{rec['engine']}。")
        if stats:
            findings += f"\n分割區域 {stats['foreground_voxels']} 體素；方法：{reconstruction['segmentation']['method']}。"
            if stats.get('physical_volume_mm3') is not None:
                findings += f" 區域體積 {stats['physical_volume_mm3']:.3f} mm³（{'合成設計間距' if source['synthetic'] else '依影像標頭一致性計算'}）。"
            else:
                findings += ' 空間資訊未驗證，未換算 mm³。'
        fields = {'indication': '影像分析與會診準備；適應症待專業人員補寫。',
            'technique': f"{source['modality']} / {source['slice_count']} 張；" + ('合成展示資料。' if source['synthetic'] else '研究／匯入影像。'),
            'comparison': '數值比對限本次已列來源及參考；未推定跨次病程。', 'findings': findings,
            'impression': '待影像覆核者依原始影像填寫正式判讀結論。',
            'limitations': '未計算 Agatston、狹窄或 LVEF；3D 表面未經臨床與手術規劃驗證。術前討論清單不代表手術適應症或治療推薦。'}
        return jsonify(schema='cardio-analysis-report-draft/1', analysis_id=ident, case_id=result['case_id'],
            run_id=result.get('run_id'), fields=fields, checks=[{'key':'analysis','label':'分析覆核','status':'ok',
                'detail':f"已由 {result['review']['actor']} 覆核研究分析；正式報告仍須獨立編輯與簽核。"}],
            sources=[], engine={'source':'reviewed_analysis_facts','model':None,'elapsed_ms':0},
            assistant_summary='依已覆核分析的來源事實整理，未將語言模型自由摘要當作判讀結論。',
            notice='尚未寫入正式報告。請人工帶入、核對、編輯及簽核。')
