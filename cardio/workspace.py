"""Source-bound preparation briefs, actual analysis jobs, and explicit cost scenarios.

These jobs use existing analysis tools. They do not simulate independent AI agents,
predict treatment outcomes, or turn machine runtime into clinical time savings.
"""
import copy
import hashlib
import json
import math
import secrets
import time
import uuid
from decimal import Decimal, ROUND_HALF_UP

from flask import jsonify, request, session, render_template, redirect, Response

from .store import now, canonical
from .imaging import sha256


def number(value, name, maximum=1_000_000_000):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError(name + '須為範圍內的有限非負數值。')
    return Decimal(str(value))


def cost_scenario(body):
    fields = {'cases_per_month', 'manual_minutes', 'assisted_minutes', 'hourly_ntd', 'monthly_cost_ntd'}
    if not isinstance(body, dict) or set(body) != fields:
        raise ValueError('請完整提供案例量、人工時間、含覆核的輔助時間、人力成本與每月導入成本。')
    v = {k: number(body[k], k, 1_000_000 if k == 'cases_per_month' else 1_000_000_000) for k in fields}
    if v['cases_per_month'] != v['cases_per_month'].to_integral_value():
        raise ValueError('每月案例量須為整數。')
    minutes = v['manual_minutes'] - v['assisted_minutes']
    per_case = minutes / Decimal(60) * v['hourly_ntd']
    gross = per_case * v['cases_per_month']
    rounded = lambda x: float(x.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
    return {'schema': 'cardio-cost-scenario/1', 'inputs': body,
            'minutes_difference_per_case': rounded(minutes), 'monthly_hours_difference': rounded(minutes*v['cases_per_month']/60),
            'gross_capacity_value_ntd': rounded(gross), 'net_scenario_value_ntd': rounded(gross-v['monthly_cost_ntd']),
            'break_even_cases': int((v['monthly_cost_ntd']/per_case).to_integral_value(rounding='ROUND_CEILING')) if per_case > 0 else None,
            'scope': 'user_entered_scenario_not_observed_savings', 'clinical_effect_verified': False,
            'notice': '情境估算：人力容量價值不等於現金收入或實際節省；輔助時間須包含資料準備、等待、修正與人工覆核。'}


class Preparation:
    def __init__(self, app, store, data, current_user, synchronous):
        self.app, self.store, self.data = app, store, data
        self.current_user, self.synchronous = current_user, synchronous
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS preparation_orders(id TEXT PRIMARY KEY, case_id TEXT NOT NULL, data TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS preparation_settings(id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            for row in db.execute('SELECT data FROM preparation_orders').fetchall():
                item = json.loads(row[0])
                if item['status'] in {'queued', 'running'}:
                    item.update(status='blocked', error='程序中斷；原任務保留，請另存新規格後執行。')
                    self.save(item, 'preparation_interrupted', db)

    def save(self, order, kind, db=None):
        if db is None:
            with self.store.connect() as conn:
                return self.save(order, kind, conn)
        order['updated_at'] = now()
        db.execute('INSERT INTO preparation_orders VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                   (order['id'], order['case_id'], canonical(order)))
        self.store.record_event(order['id'], kind, {'actor': order.get('executed_by', order['actor']),
                                'status': order['status'], 'spec_sha256': order['spec_sha256']}, db)

    def get(self, ident):
        with self.store.connect() as db:
            row = db.execute('SELECT data FROM preparation_orders WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        return json.loads(row[0])

    def source(self, case):
        files = list(case['slices'])
        if case.get('provided_labels'):
            files.append(case['provided_labels'])
        paths = [self.app.analysis._source_path(p) for p in files]
        return {'case_id': case['id'], 'modality': case['modality'], 'synthetic': bool(case.get('synthetic')),
                'files': [{'path': name, 'sha256': sha256(p)} for name, p in zip(files, paths)],
                'case_metadata_sha256': hashlib.sha256(canonical(case).encode()).hexdigest()}

    def create(self, body, actor):
        allowed = {'case_id', 'goal', 'question', 'context', 'settings'}
        if not isinstance(body, dict) or set(body) != allowed:
            raise ValueError('任務只接受病例、目標、臨床問題、確認欄位與分析設定。')
        text = self.app.analysis._text
        case = self.store.case(text(body['case_id'], 100, '病例代碼'))
        goal, question = text(body['goal'], 1000, '任務目標'), text(body['question'], 2000, '會診問題')
        context = body['context']
        if not isinstance(context, dict) or set(context) != {'acute', 'history_complete', 'consent'}:
            raise ValueError('請提供急性狀態、背景完整性與資料授權三項確認。')
        if any(v is not None and type(v) is not bool for v in context.values()):
            raise ValueError('確認欄位僅接受 true、false 或 null。')
        settings = body['settings']
        if not isinstance(settings, dict) or set(settings) - {'mode', 'center', 'threshold', 'segmentation_mode', 'reference_class', 'use_llm', 'model'}:
            raise ValueError('分析設定含未定義欄位。')
        s = {'mode': 'inspect', 'center': len(case['slices'])//2, 'threshold': 128,
             'segmentation_mode': 'provided_reference' if case.get('provided_labels') else 'threshold',
             'reference_class': 0, 'use_llm': False, 'model': '', **settings}
        if s['mode'] not in {'inspect', 'demo', 'model'} or s['segmentation_mode'] not in {'threshold', 'provided_reference', 'registered_model'}:
            raise ValueError('辨識或分割模式無效。')
        if s['mode'] == 'demo' and not case.get('synthetic'):
            raise ValueError('研究／匯入影像不可使用合成候選模式。')
        if s['segmentation_mode'] == 'provided_reference' and not case.get('provided_labels'):
            raise ValueError('本病例沒有提供的研究標籤。')
        if type(s['center']) is not int or not 0 <= s['center'] < len(case['slices']):
            raise ValueError('中心切片無效。')
        if type(s['reference_class']) is not int or s['reference_class'] not in (0, 1, 2, 3):
            raise ValueError('參考類別無效。')
        if type(s['threshold']) not in (int, float) or not math.isfinite(s['threshold']) or not -32768 <= s['threshold'] <= 65535:
            raise ValueError('閾值無效。')
        if type(s['use_llm']) is not bool:
            raise ValueError('use_llm 須為布林值。')
        s['model'] = text(s['model'], 180, '文字模型', empty=True)
        if s['use_llm'] and (not s['model'] or not case.get('synthetic')):
            raise ValueError('流程摘要模型僅可選已指定模型的合成案例；研究影像使用規則摘要。')
        source = self.source(case)
        spec = {'goal': goal, 'question': question, 'context': copy.deepcopy(context), 'settings': s, 'source': source}
        order = {'schema': 'cardio-preparation-order/1', 'id': uuid.uuid4().hex,
                 'case_id': case['id'], 'actor': actor, 'created_at': now(), 'status': 'draft', 'version': 1,
                 'spec': spec, 'spec_sha256': hashlib.sha256(canonical(spec).encode()).hexdigest(),
                 'analysis_id': None, 'machine_elapsed_ms': None, 'error': None,
                 'clinical_effect_verified': False, 'device_execution_verified': False}
        self.save(order, 'preparation_spec_created')
        return order

    def blockers(self, item):
        c = item['spec']['context']
        missing = []
        if hashlib.sha256(canonical(item['spec']).encode()).hexdigest() != item['spec_sha256']:
            missing.append('規格雜湊不一致，拒絕執行')
        if c['acute'] is not False:
            missing.append('已標示急性警訊，交由人員優先處理' if c['acute'] is True else '急性狀態尚未確認')
        if c['history_complete'] is not True:
            missing.append('背景資料尚未確認完整')
        if c['consent'] is not True:
            missing.append('資料授權尚未確認')
        try:
            if self.source(self.store.case(item['case_id'])) != item['spec']['source']:
                missing.append('來源或病例資訊已變更，請另存新規格')
        except (OSError, ValueError):
            missing.append('無法核對來源檔案')
        return missing

    def submit(self, ident, version, actor):
        with self.store.connect() as db:
            item = self.get(ident)
            if type(version) is not int or item['version'] != version or item['status'] != 'draft':
                raise ValueError('任務狀態或版本已改變，不可重複執行。')
            reasons = self.blockers(item)
            if reasons:
                raise ValueError('；'.join(reasons))
            item.update(status='queued', version=2, executed_by=actor, queued_at=now())
            self.save(item, 'preparation_queued', db)
        if self.synchronous:
            self.execute(ident)
        else:
            self.app.pool.submit(self.execute, ident)
        return self.present(self.get(ident))

    def execute(self, ident):
        item = self.get(ident)
        if item['status'] != 'queued':
            return
        begin = time.perf_counter()
        try:
            reasons = self.blockers(item)
            if reasons:
                raise ValueError('；'.join(reasons))
            item.update(status='running', started_at=now(), version=3)
            self.save(item, 'preparation_started')
            body = {'case_id': item['case_id'], **item['spec']['settings']}
            result = self.app.analysis.execute(body, item['executed_by'])
            # Preserve the output ID before any secondary preparation stage.
            item['analysis_id'] = result['id']
            self.save(item, 'preparation_analysis_saved')
            checklist = self.app.discussion.assess(result, {'acute': False})
            result = self.app.analysis.attach_discussion(result['id'], checklist, item['executed_by'], result['version'])
            blocked = [s for s in result['stages'] if s['status'] == 'blocked']
            expected = [f['sha256'] for f in item['spec']['source']['files'][:len(self.store.case(item['case_id'])['slices'])]]
            if result['source'].get('slice_sha256') != expected or self.blockers(item):
                raise ValueError('分析期間來源或規格已變更，結果須重新核對。')
            item.update(status='needs_attention' if blocked else 'pending_review', error='部分工具受阻，請查看原始分析。' if blocked else None)
        except Exception as exc:
            item.update(status='blocked', error=str(exc)[:500])
        finally:
            item.update(machine_elapsed_ms=round((time.perf_counter()-begin)*1000, 3), version=item['version']+1)
            self.save(item, 'preparation_finished')

    def present(self, item):
        item = copy.deepcopy(item)
        item['blockers'] = self.blockers(item)
        if item['analysis_id']:
            try:
                result = self.app.analysis.get(item['analysis_id'])
                integrity = self.app.analysis.integrity(result)
                item['analysis'] = {'id': result['id'], 'status': result['status'], 'version': result['version'],
                    'integrity': integrity, 'review': result['review'], 'stages': result['stages'],
                    'source_kind': 'synthetic' if result['source']['synthetic'] else 'research_or_imported',
                    'language_source': (result.get('language') or {}).get('source'),
                    'candidate_count': len((result.get('recognition') or {}).get('candidates', [])) if result.get('recognition') else None,
                    'recognition_engine': (result.get('recognition') or {}).get('engine'),
                    'foreground_voxels': ((result.get('reconstruction') or {}).get('statistics') or {}).get('foreground_voxels')}
                if result['status'] in {'approved', 'rejected'}:
                    item['status'] = result['status']
                if not integrity['source_current'] or not integrity['audit_valid'] or item['blockers']:
                    item['status'] = 'needs_attention'
            except (ValueError, OSError, KeyError):
                item['error'] = '分析來源無法核對，請檢查本機紀錄。'
        return item

    def board(self):
        with self.store.connect() as db:
            rows = db.execute('SELECT data FROM preparation_orders ORDER BY rowid DESC').fetchall()
            scenario = db.execute("SELECT data FROM preparation_settings WHERE id='cost'").fetchone()
        items = [self.present(json.loads(r[0])) for r in rows]
        return {'orders': items, 'model_queue': self.app.analysis.planner.gate.snapshot(),
                'cost_scenario': json.loads(scenario[0]) if scenario else None,
                'summary': {'orders': len(items), 'waiting_review': sum(i['status']=='pending_review' for i in items),
                            'attention': sum(bool(i['blockers']) or i['status'] in {'blocked','needs_attention'} for i in items),
                            'reviewed': sum(i['status']=='approved' for i in items)},
                'runtime_scope': 'this_workstation', 'clinical_effect_verified': False}


def register_workspace(app, store, data, current_user, synchronous=False):
    prep = Preparation(app, store, data, current_user, synchronous)
    app.preparation = prep

    @app.get('/workspace')
    def workspace_page():
        if not current_user():
            return redirect('/')
        session.setdefault('csrf', secrets.token_urlsafe(32))
        return render_template('workspace.html', csrf=session['csrf'])

    @app.get('/api/workspace')
    def workspace_board():
        return jsonify(prep.board())

    @app.post('/api/workspace/orders')
    def workspace_create():
        return jsonify(prep.present(prep.create(request.get_json(), current_user()['username']))), 201

    @app.post('/api/workspace/orders/<ident>/execute')
    def workspace_execute(ident):
        if current_user()['role'] not in {'admin', 'radiologist'}:
            raise PermissionError('執行影像分析需要影像覆核角色。')
        body = request.get_json()
        if not isinstance(body, dict) or set(body) != {'version'}:
            raise ValueError('請提供目前任務版本。')
        return jsonify(prep.submit(ident, body['version'], current_user()['username'])), 202

    @app.get('/api/workspace/orders/<ident>/export')
    def workspace_export(ident):
        item = prep.present(prep.get(ident))
        payload = {'schema': 'cardio-preparation-export/1', 'order': item,
                   'events': store.events(ident), 'audit': store.verify(),
                   'analysis': app.analysis.export(item['analysis_id']) if item['analysis_id'] else None,
                   'notice': '含研究代碼與使用者填寫的背景；公開版僅本分頁保存，未連接外部醫院或裝置。'}
        return Response(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), mimetype='application/json',
                        headers={'Content-Disposition': f'attachment; filename="preparation-{item["id"][:8]}.json"'})

    @app.post('/api/workspace/cost-scenario')
    def workspace_cost():
        result = cost_scenario(request.get_json())
        result.update(actor=current_user()['username'], at=now())
        with store.connect() as db:
            db.execute("INSERT INTO preparation_settings VALUES ('cost',?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (canonical(result),))
            store.record_event('preparation-cost', 'cost_scenario_saved', result, db)
        return jsonify(result)
