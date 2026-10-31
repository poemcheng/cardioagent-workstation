import io
import json
import os
import secrets
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
from flask import Flask, jsonify, request, session, render_template, Response, send_file, redirect
from werkzeug.exceptions import HTTPException
from .store import Store
from .planner import Planner
from .imaging import Detector, png_bytes, read_upload
from .agent import Agent
from .hospital import Hospital, Conflict
from .auth import Auth
from .competition import Competition, ServiceUnavailable
from datetime import timedelta


ROOT = Path(__file__).resolve().parents[1]


def create_app(data_dir=None, synchronous=False, recover_interrupted=True, access_control=True, anonymous_actor=None):
    from .bundled_assets import ensure_demo_assets
    ensure_demo_assets(ROOT)
    app = Flask(__name__, template_folder=str(ROOT/"templates"), static_folder=str(ROOT/"static"))
    data = Path(data_dir or os.environ.get("CARDIO_DATA_DIR", ROOT/"data")).resolve()
    data.mkdir(parents=True, exist_ok=True)
    key_path = data/"session.key"
    if not key_path.exists():
        key_path.write_bytes(secrets.token_bytes(32))
        key_path.chmod(0o600)
    app.secret_key = key_path.read_bytes()
    app.config.update(MAX_CONTENT_LENGTH=48*1024*1024, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict", TRUSTED_HOSTS=["127.0.0.1", "localhost"])
    app.permanent_session_lifetime = timedelta(minutes=30)
    config = json.loads((ROOT/"config.json").read_text(encoding="utf-8"))
    store = Store(data/"cardio.sqlite3")
    if recover_interrupted:
        store.recover_interrupted()
    planner, detector = Planner(config), Detector(ROOT, config)
    agent = Agent(store, ROOT, data, detector, planner)
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cardio-agent")
    actions = threading.RLock()
    app.store, app.agent, app.pool, app.detector = store, agent, pool, detector
    from .seed import seed
    seed(store, data, ROOT)
    hospital, auth = Hospital(store), Auth(store)
    hospital.seed()
    app.hospital, app.auth = hospital, auth
    competition = Competition(ROOT, store, planner, detector)
    app.competition = competition

    def current_user():
        return auth.user(session.get('username')) if access_control else {'username': anonymous_actor or 'test-only', 'role': 'radiologist' if anonymous_actor else 'admin'}

    def launch(ident):
        if synchronous:
            agent.execute(ident)
        else:
            pool.submit(agent.execute, ident)

    @app.before_request
    def guard():
        if request.remote_addr not in {"127.0.0.1", "::1", None}:
            return jsonify(error="只接受本機連線"), 403
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            token = request.headers.get("X-CSRF-Token", "")
            if not session.get("csrf") or not secrets.compare_digest(token, session["csrf"]):
                return jsonify(error="CSRF 驗證失敗；請重新整理頁面"), 403
        public = {'/api/session','/api/health','/api/auth/status','/api/auth/login','/api/auth/setup'}
        if request.path.startswith(('/api/','/reference/')) and request.path not in public:
            user = current_user()
            if not user:
                return jsonify(error='請先登入本機工作站'), 401
            if request.method in {'POST','PUT','PATCH','DELETE'}:
                role = user['role']
                if request.path == '/api/auth/logout':
                    return None
                if request.path.startswith('/api/auth/users') and role != 'admin':
                    return jsonify(error='此操作需要管理者角色'), 403
                if '/report' in request.path or request.path.endswith('/review') or request.path in {'/api/demo/prepare', '/api/demo/transcribe', '/api/analysis/phantom'} or request.path.startswith('/api/analyses'):
                    if role not in {'admin','radiologist'}:
                        return jsonify(error='此操作需要影像覆核角色'), 403
                elif role == 'viewer':
                    return jsonify(error='檢視角色不可修改資料'), 403

    @app.after_request
    def headers(response):
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"})
        return response

    @app.errorhandler(Exception)
    def errors(exc):
        if isinstance(exc, HTTPException):
            return jsonify(error=exc.description), exc.code
        if isinstance(exc, KeyError):
            return jsonify(error="找不到病例或工作"), 404
        if isinstance(exc, PermissionError):
            return jsonify(error=str(exc)), 403
        if isinstance(exc, Conflict):
            return jsonify(error=str(exc)), 409
        if isinstance(exc, (ValueError, TypeError, OSError)):
            return jsonify(error=str(exc)[:500]), 400
        app.logger.exception("request failed")
        return jsonify(error="伺服器錯誤，請查閱本機紀錄"), 500

    @app.get("/")
    def index():
        session.setdefault("csrf", secrets.token_urlsafe(32))
        if not current_user():
            return render_template('login.html', csrf=session['csrf'], setup=not auth.users())
        return render_template("index.html", csrf=session["csrf"])

    @app.get('/research')
    def research():
        if not current_user():
            return redirect('/')
        session.setdefault('csrf', secrets.token_urlsafe(32))
        return render_template('research.html', csrf=session['csrf'])

    @app.get('/api/auth/status')
    def auth_status():
        return jsonify(user=current_user(), setup_required=not auth.users())

    @app.post('/api/auth/setup')
    def setup_user():
        b=request.get_json()
        user=auth.add(b.get('username'), b.get('password'), 'admin', initial=True)
        session.clear();session['username']=user['username'];session['csrf']=secrets.token_urlsafe(32);session.permanent=True
        return jsonify(user=user)

    @app.post('/api/auth/login')
    def login():
        b=request.get_json();user=auth.login(b.get('username'), b.get('password'))
        session.clear();session['username']=user['username'];session['csrf']=secrets.token_urlsafe(32);session.permanent=True
        return jsonify(user=user)

    @app.post('/api/auth/logout')
    def logout():
        store.record_event('auth','logout',{'actor':current_user()['username']});session.clear()
        return jsonify(ok=True)

    @app.route('/api/auth/users',methods=['GET','POST'])
    def users():
        if current_user()['role']!='admin':
            raise PermissionError('此操作需要管理者角色')
        if request.method=='GET':
            return jsonify(auth.users())
        b=request.get_json()
        return jsonify(auth.add(b.get('username'),b.get('password'),b.get('role'),current_user()['username'])),201

    @app.route('/api/hospital/studies',methods=['GET','POST'])
    def studies():
        if request.method=='GET':
            return jsonify(hospital.list())
        return jsonify(hospital.register(request.get_json(),current_user()['username'])),201

    @app.get('/api/hospital/studies/<ident>')
    def study_detail(ident):
        result=hospital.detail(ident)
        store.record_event(ident,'study_opened',{'actor':current_user()['username']})
        return jsonify(result)

    @app.post('/api/hospital/studies/<ident>/report')
    def report_edit(ident):
        return jsonify(hospital.edit_report(ident,request.get_json(),current_user()['username']))

    @app.post('/api/hospital/studies/<ident>/report/sign')
    def report_sign(ident):
        return jsonify(hospital.sign_report(ident,request.get_json(),current_user()['username']))

    @app.post('/api/hospital/studies/<ident>/report/addendum')
    def report_addendum(ident):
        return jsonify(hospital.addendum(ident,request.get_json(),current_user()['username']))

    @app.get('/api/hospital/studies/<ident>/history')
    def report_history(ident):
        return jsonify(revisions=hospital.revisions(ident),events=store.events(ident))

    @app.get('/api/hospital/studies/<ident>/fhir')
    def report_fhir(ident):
        payload=hospital.fhir(ident)
        store.record_event(ident,'fhir_exported',{'actor':current_user()['username']})
        return Response(json.dumps(payload,ensure_ascii=False,indent=2),mimetype='application/fhir+json',headers={'Content-Disposition':f'attachment; filename="local-report-{ident[:8]}.json"'})

    @app.get("/api/session")
    def session_info():
        session.setdefault("csrf", secrets.token_urlsafe(32))
        return jsonify(csrf=session["csrf"])

    @app.get("/api/health")
    def health():
        try:
            manifest, _ = detector.manifest()
            vision = {"configured": True, "name": manifest["name"], "sha256": manifest["sha256"]}
        except Exception as exc:
            vision = {"configured": False, "reason": str(exc)}
        return jsonify(status="ok", local_only=True, vision=vision, planner_model=config.get("planner_model"), npu_verified=False, hardware_verified=False, audit=store.verify())

    @app.get("/api/lemonade")
    def lemonade():
        try:
            return jsonify(available=True, models=planner.models())
        except Exception as exc:
            return jsonify(available=False, error=str(exc)[:300])

    @app.get('/api/demo/status')
    def demo_status():
        return jsonify(competition.status())

    @app.get('/api/demo/sources/<ident>')
    def demo_source(ident):
        return jsonify(competition.source(ident))

    @app.post('/api/demo/prepare')
    def demo_prepare():
        # Read a run snapshot and append audit only. Never hold the workflow
        # mutation lock while waiting for an optional model service.
        return jsonify(competition.prepare(request.get_json(), current_user()['username']))

    @app.post('/api/demo/transcribe')
    def demo_transcribe():
        upload = request.files.get('file')
        if upload is None:
            raise ValueError('請選擇 WAV 音訊。')
        raw = upload.read(8 * 1024 * 1024 + 1)
        try:
            return jsonify(competition.transcribe(request.form.get('case_id', ''), raw, request.form.get('model', ''), current_user()['username']))
        except ServiceUnavailable as exc:
            return jsonify(error=str(exc), available=False, text=None), 503

    @app.get("/api/cases")
    def cases():
        return jsonify([{k: v for k, v in c.items() if k != "slices"} | {"slice_count": len(c["slices"]), "last_run_id": store.latest(c["id"])} for c in store.cases()])

    @app.get("/api/cases/<ident>")
    def case_info(ident):
        c = store.case(ident)
        return jsonify({k: v for k, v in c.items() if k != "slices"} | {"slice_count": len(c["slices"])})

    @app.get("/api/cases/<ident>/slices/<int:index>")
    def slice_image(ident, index):
        c = store.case(ident)
        if index >= len(c["slices"]):
            raise ValueError("切片索引超出範圍")
        arr = np.load(data/c["slices"][index], allow_pickle=False)
        center = float(request.args.get("center", 40)); width = float(request.args.get("width", 400))
        if not np.isfinite(center) or not np.isfinite(width) or not 1 <= width <= 10000:
            raise ValueError("窗位參數無效")
        return Response(png_bytes(arr, is_hu=c.get("is_hu", False), center=center, width=width), mimetype="image/png")

    @app.post("/api/cases")
    def upload():
        files = request.files.getlist("images")
        modality = request.form.get("modality", "UNKNOWN")
        if modality not in {"CT", "MRI", "UNKNOWN"} or not 1 <= len(files) <= 64:
            raise ValueError("請選擇 1–64 張影像及正確類型。")
        parsed = []
        total_pixels = 0
        for file in files:
            raw = file.read(8*1024*1024+1)
            if len(raw) > 8*1024*1024:
                raise ValueError("單張影像上限 8 MB")
            parsed.append(read_upload(raw, file.filename or "image", modality))
            total_pixels += parsed[-1][0].size
            if total_pixels > 32_000_000:
                raise ValueError("序列像素總量上限 3,200 萬，請分段匯入。")
        if len({(a.shape, m["modality"], m["is_hu"]) for a,m in parsed}) != 1:
            raise ValueError("序列尺寸、類型或 HU 格式不一致")
        if len(files)>1 and request.form.get("order_confirmed") != "true":
            raise ValueError("須確認上傳順序為前、中、後的實際切片順序。")
        from .volume import assemble_geometry
        parsed, geometry = assemble_geometry(parsed)
        ident = "upload-" + uuid.uuid4().hex[:12]
        folder = data/"images"/ident
        folder.mkdir(parents=True)
        slices = []
        for i,(arr,_) in enumerate(parsed):
            p = folder/f"{i:04}.npy"; np.save(p, arr, allow_pickle=False); slices.append(str(p.relative_to(data)))
        case = {"id": ident, "label": "本機匯入影像", "modality": parsed[0][1]["modality"], "is_hu": parsed[0][1]["is_hu"], "synthetic": False, "source": parsed[0][1]["source"], "slices": slices, "reference_boxes": {}, "geometry": geometry, "source_detail": "上傳檔名及 DICOM 病人欄位未保存；像素仍可能含燒錄文字，須先去識別。"}
        store.put_case(case)
        return jsonify(id=ident), 201

    @app.post("/api/runs")
    def start():
        body = request.get_json()
        with actions:
            run = agent.create(store.case(body["case_id"]), body.get("context", {}), body.get("planner", "rules"), body.get("mode", "model"), body.get("center", 0))
            launch(run["id"])
        return jsonify(id=run["id"]), 202

    @app.get("/api/runs/<ident>")
    def run_info(ident):
        return jsonify(store.run(ident) | {"tasks": store.tasks(ident), "events": store.events(ident)})

    @app.post("/api/runs/<ident>/resume")
    def resume(ident):
        body = request.get_json()
        with actions:
            run = agent.resume(ident, body.get("context", {}), body.get("version"))
            launch(ident)
        return jsonify(id=run["id"]), 202

    @app.post("/api/runs/<ident>/review")
    def review(ident):
        b = request.get_json()
        with actions:
            run = agent.review(ident, b.get("decision"), current_user()['username'] if access_control else b.get("reviewer", ""), b.get("reason", ""), b.get("version"))
        return jsonify(run)

    @app.get("/api/runs/<ident>/export")
    def export(ident):
        r = store.run(ident)
        payload = {"schema": "cardio-evidence/1", "run": r, "tasks": store.tasks(ident), "events": store.events(ident), "audit": store.verify(), "notice": "包含研究病例代碼及背景，僅在授權範圍分享。無原始影像／DICOM header。"}
        return Response(json.dumps(payload, ensure_ascii=False, indent=2), mimetype="application/json", headers={"Content-Disposition": f'attachment; filename="cardio-evidence-{ident[:8]}.json"'})

    @app.get("/reference/<name>")
    def reference(name):
        if name not in {"mri_result_comparison.png", "cardioagent_01_system_overview.png", "cardioagent_03_model_summary.png"}:
            raise KeyError(name)
        return send_file(ROOT/"assets/reference"/name)

    from .integration_routes import register_analysis_routes
    register_analysis_routes(app, ROOT, data, store, detector, planner, hospital, current_user, actions)
    return app
