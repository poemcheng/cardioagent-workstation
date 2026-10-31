"""Local RIS workflow. Clinical content is written and approved by a person."""
import json
import re
import uuid
from datetime import datetime
from .store import canonical, now

REPORT_FIELDS = ('indication', 'technique', 'comparison', 'findings', 'impression', 'limitations')


class Conflict(ValueError):
    pass


class Hospital:
    def __init__(self, store):
        self.store = store
        self.signature_kind = 'local authenticated approval; not a certificate-based hospital signature'

    def seed(self):
        """Administrative identifiers are fictional, never inferred from pixels."""
        with self.store.connect() as db:
            for i, case in enumerate(self.store.cases(), 1):
                if case['id'] not in {'demo-ct-positive','demo-ct-zero','research-mri-01'}:
                    continue
                if db.execute('SELECT 1 FROM studies WHERE case_id=?', (case['id'],)).fetchone():
                    continue
                patient_id = f'RESEARCH-{i:03}'
                patient = {'id': patient_id, 'name': f'研究個案 {i:03}', 'sex': 'unknown', 'birth_date': None, 'administrative_demo': True}
                db.execute('INSERT OR IGNORE INTO patients VALUES (?,?)', (patient_id, canonical(patient)))
                study = {'id': uuid.uuid4().hex, 'patient_id': patient_id, 'case_id': case['id'], 'accession': f'LOCAL-{i:04}', 'description': case['label'], 'modality': case['modality'], 'department': '研究影像室', 'referrer': '研究資料匯入', 'priority': 'routine', 'created_at': now(), 'scheduled_date': None, 'clinical_question': '', 'administrative_demo': True, 'external_connected': False, 'version': 1}
                db.execute('INSERT INTO studies VALUES (?,?,?,?)', (study['id'], study['accession'], case['id'], canonical(study)))

    def list(self):
        with self.store.connect() as db:
            studies = [json.loads(r[0]) for r in db.execute('SELECT data FROM studies ORDER BY accession')]
        return [self.detail(s['id']) for s in studies]

    def detail(self, ident):
        with self.store.connect() as db:
            row = db.execute('SELECT data FROM studies WHERE id=?', (ident,)).fetchone()
            if not row:
                raise KeyError(ident)
            study = json.loads(row[0])
            patient = json.loads(db.execute('SELECT data FROM patients WHERE id=?', (study['patient_id'],)).fetchone()[0])
            row = db.execute('SELECT data FROM reports WHERE study_id=?', (ident,)).fetchone()
            report = json.loads(row[0]) if row else None
        case = self.store.case(study['case_id'])
        return study | {'patient': patient, 'report': report, 'workflow_status': report['status'] if report else 'available', 'slice_count': len(case['slices']), 'image_is_synthetic': case.get('synthetic', False), 'series': [{'id': case['id'], 'description': case['label'], 'modality': case['modality'], 'instances': len(case['slices']), 'spacing_known': False}], 'last_run_id': self.store.latest(case['id'])}

    def register(self, body, actor):
        case = self.store.case(body.get('case_id', ''))
        patient_id = self.text(body.get('patient_id'), 64)
        accession = self.text(body.get('accession'), 64)
        name = self.text(body.get('patient_name'), 80)
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', patient_id) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', accession) or not name:
            raise ValueError('病歷代碼、檢查單號與顯示名稱必填；代碼只允許英數及 _ . -。')
        sex = body.get('sex', 'unknown')
        if sex not in {'male', 'female', 'other', 'unknown'}:
            raise ValueError('性別代碼無效。')
        birth = body.get('birth_date') or None
        if birth:
            datetime.strptime(birth, '%Y-%m-%d')
        priority = body.get('priority', 'routine')
        if priority not in {'routine', 'urgent'}:
            raise ValueError('優先順序無效。')
        patient = {'id': patient_id, 'name': name, 'sex': sex, 'birth_date': birth, 'administrative_demo': True}
        study = {'id': uuid.uuid4().hex, 'patient_id': patient_id, 'case_id': case['id'], 'accession': accession, 'description': self.text(body.get('description'), 120) or case['label'], 'modality': case['modality'], 'department': self.text(body.get('department'), 80), 'referrer': self.text(body.get('referrer'), 80), 'priority': priority, 'clinical_question': self.text(body.get('clinical_question'), 2000), 'created_at': now(), 'scheduled_date': None, 'administrative_demo': True, 'external_connected': False, 'version': 1}
        with self.store.connect() as db:
            if db.execute('SELECT 1 FROM studies WHERE accession=? OR case_id=?', (accession, case['id'])).fetchone():
                raise Conflict('檢查單號或影像病例已建檔。')
            existing = db.execute('SELECT data FROM patients WHERE id=?', (patient_id,)).fetchone()
            if existing and json.loads(existing[0]) != patient:
                raise Conflict('此病歷代碼已存在且人口學資料不同，禁止自動覆寫。')
            db.execute('INSERT OR IGNORE INTO patients VALUES (?,?)', (patient_id, canonical(patient)))
            db.execute('INSERT INTO studies VALUES (?,?,?,?)', (study['id'], accession, case['id'], canonical(study)))
            self.store.record_event(study['id'], 'study_registered', {'actor': actor, 'accession': accession}, db)
        return self.detail(study['id'])

    @staticmethod
    def text(value, limit):
        if value is None:
            return ''
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f'文字欄位須為字串且不超過 {limit} 字。')
        return value.strip()

    def edit_report(self, ident, body, actor):
        with self.store.connect() as db:
            study = self.detail(ident)
            previous = study['report']
            version = previous['version'] if previous else 0
            if type(body.get('version')) is not int or body['version'] != version:
                raise Conflict('報告版本已變更，請重新載入。')
            if previous and previous['status'] in {'final', 'amended'}:
                raise Conflict('已簽核報告不可覆寫，請新增補充報告。')
            fields = body.get('fields')
            if not isinstance(fields, dict) or set(fields) != set(REPORT_FIELDS):
                raise ValueError('報告須包含適應症、技術、比較、所見、結論及限制六欄。')
            status = body.get('status', 'draft')
            if status not in {'draft', 'preliminary'}:
                raise ValueError('此操作只可儲存草稿或初步報告。')
            fields = {k: self.text(v, 12000) for k, v in fields.items()}
            if status == 'preliminary' and (not fields['findings'] or not fields['impression']):
                raise ValueError('送交初步報告前，影像所見與結論須由人員填寫。')
            report = {'study_id': ident, 'id': previous['id'] if previous else uuid.uuid4().hex, 'status': status, 'version': version+1, 'fields': fields, 'created_at': previous['created_at'] if previous else now(), 'updated_at': now(), 'author': actor, 'addenda': [], 'signature': None}
            self._save_report(db, report, actor, 'report_saved')
        return report

    def sign_report(self, ident, body, actor):
        with self.store.connect() as db:
            report = self.detail(ident)['report']
            if not report or type(body.get('version')) is not int or report['version'] != body.get('version'):
                raise Conflict('報告不存在或版本已變更。')
            if report['status'] != 'preliminary':
                raise Conflict('請先儲存並送交初步報告，再進行簽核。')
            if body.get('review_confirmed') is not True:
                raise ValueError('須明確確認已核對本次檢查、影像與報告內容。')
            if not report['fields']['findings'] or not report['fields']['impression']:
                raise ValueError('報告缺少影像所見或結論。')
            report['status'] = 'final'; report['version'] += 1; report['updated_at'] = now()
            report['signature'] = {'username': actor, 'at': now(), 'kind': self.signature_kind}
            self._save_report(db, report, actor, 'report_signed')
        return report

    def addendum(self, ident, body, actor):
        with self.store.connect() as db:
            report = self.detail(ident)['report']
            if not report or type(body.get('version')) is not int or report['version'] != body.get('version'):
                raise Conflict('報告不存在或版本已變更。')
            if report['status'] not in {'final', 'amended'}:
                raise Conflict('僅已簽核報告可加補充說明。')
            text, reason = self.text(body.get('text'), 12000), self.text(body.get('reason'), 1000)
            if not text or not reason:
                raise ValueError('補充內容與原因必填。')
            report['addenda'].append({'text': text, 'reason': reason, 'author': actor, 'at': now()})
            report['status'] = 'amended'; report['version'] += 1; report['updated_at'] = now()
            self._save_report(db, report, actor, 'report_addendum')
        return report

    def _save_report(self, db, report, actor, kind):
        db.execute('INSERT INTO reports VALUES (?,?) ON CONFLICT(study_id) DO UPDATE SET data=excluded.data', (report['study_id'], canonical(report)))
        db.execute('INSERT INTO report_revisions VALUES (?,?,?)', (report['study_id'], report['version'], canonical(report)))
        self.store.record_event(report['study_id'], kind, {'actor': actor, 'version': report['version'], 'status': report['status']}, db)

    def revisions(self, ident):
        self.detail(ident)
        with self.store.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT data FROM report_revisions WHERE study_id=? ORDER BY version', (ident,))]

    def fhir(self, ident):
        study = self.detail(ident); patient = study['patient']; report = study['report']
        patient_fhir_id = 'p-'+uuid.uuid5(uuid.NAMESPACE_URL, patient['id']).hex
        patient_resource = {'resourceType': 'Patient', 'id': patient_fhir_id, 'identifier': [{'system': 'urn:cardioagent:local:mrn', 'value': patient['id']}], 'name': [{'text': patient['name']}], 'gender': patient['sex']}
        if patient['birth_date']:
            patient_resource['birthDate'] = patient['birth_date']
        subject = {'reference': 'Patient/'+patient_fhir_id}
        order = {'resourceType': 'ServiceRequest', 'id': ident, 'status': 'completed' if report and report['status'] in {'final', 'amended'} else 'active', 'intent': 'order', 'priority': study['priority'], 'code': {'text': study['description']}, 'subject': subject, 'authoredOn': study['created_at'], 'identifier': [{'system': 'urn:cardioagent:local:accession', 'value': study['accession']}]}
        resources = [patient_resource, order]
        if report:
            status = {'draft': 'partial', 'preliminary': 'preliminary', 'final': 'final', 'amended': 'amended'}[report['status']]
            narrative = '\n\n'.join(f'{k}: {v}' for k,v in report['fields'].items())
            if report['addenda']:
                narrative += '\n\n'+'\n'.join('Addendum: '+a['text'] for a in report['addenda'])
            import base64
            dr = {'resourceType': 'DiagnosticReport', 'id': report['id'], 'status': status, 'code': {'text': study['description']}, 'subject': subject, 'basedOn': [{'reference': 'ServiceRequest/'+ident}], 'effectiveDateTime': study['created_at'], 'issued': report['updated_at'], 'presentedForm': [{'contentType': 'text/plain', 'title': 'LOCAL RESEARCH REPORT', 'data': base64.b64encode(narrative.encode()).decode()}]}
            if report['fields']['impression']:
                dr['conclusion'] = report['fields']['impression']
            resources.append(dr)
        # No fabricated DICOM UID, ImagingStudy resource, PACS URL or remote write.
        urls = {r['resourceType']+'/'+r['id']: f'urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, r["resourceType"]+"/"+r["id"])}' for r in resources}
        def resolve(value):
            if isinstance(value, dict):
                for key,item in value.items():
                    if key == 'reference' and item in urls:
                        value[key] = urls[item]
                    else:
                        resolve(item)
            elif isinstance(value, list):
                for item in value: resolve(item)
        for resource in resources: resolve(resource)
        return {'resourceType': 'Bundle', 'type': 'collection', 'timestamp': now(), 'meta': {'tag': [{'system': 'urn:cardioagent:environment', 'code': 'local-research', 'display': 'Local research export; no hospital connection'}]}, 'entry': [{'fullUrl': urls[r['resourceType']+'/'+r['id']], 'resource': r} for r in resources]}
