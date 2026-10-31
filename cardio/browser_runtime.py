"""Run the unchanged Flask/NumPy workflow inside a visitor's Python Web Worker.

There is no listening socket or shared public database. A Flask test client is
an in-process WSGI transport here, not a substitute for analysis execution.
"""
import base64
import io
import json

from flask import jsonify, request
from werkzeug.datastructures import MultiDict

from .app import create_app


class BrowserRuntime:
    def __init__(self, data_dir='/session'):
        self.app = create_app(data_dir=data_dir, synchronous=True,
                              access_control=False, anonymous_actor='browser-visitor')
        self.client = self.app.test_client()
        self.app.hospital.signature_kind = 'browser-local demo attestation; no authenticated clinician or hospital signature'

        def no_remote_models():
            raise ValueError('公開瀏覽器模式未連接 Lemonade；模型推論請使用 AMD AI PC 本機版。')

        self.app.analysis.planner.models = no_remote_models

        @self.app.before_request
        def browser_scope():
            if request.path.startswith('/api/auth/') and request.path != '/api/auth/status':
                return jsonify(error='此為個人分頁的展示環境，不提供共用帳號管理。'), 403

        @self.app.after_request
        def label_demo(response):
            if request.path == '/api/auth/status':
                body = response.get_json()
                body.update(setup_required=False, runtime='browser_python_wasm',
                            shared_server=False, persistence='this_tab_only')
                response.set_data(json.dumps(body, ensure_ascii=False))
            return response

    def request(self, serialized):
        payload = json.loads(serialized)
        path = payload['path']
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('Only application-relative paths are allowed.')
        method = payload.get('method', 'GET')
        options = {'method': method, 'headers': payload.get('headers', {})}
        if payload.get('multipart') is not None:
            data = MultiDict()
            for part in payload['multipart']:
                if 'filename' in part:
                    data.add(part['name'], (io.BytesIO(base64.b64decode(part['base64'])), part['filename'], part.get('type')))
                else:
                    data.add(part['name'], part['value'])
            options.update(data=data, content_type='multipart/form-data')
        elif payload.get('body') is not None:
            options['data'] = payload['body']
        response = self.client.open(path, **options)
        return json.dumps({'status': response.status_code,
                          'headers': {k: v for k, v in response.headers.items() if k.lower() not in {'set-cookie', 'content-length'}},
                          'base64': base64.b64encode(response.data).decode('ascii')})

    def close(self):
        self.app.pool.shutdown(wait=True)
