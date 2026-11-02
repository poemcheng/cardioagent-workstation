"""Create source-pinned clinical text cases and an explicit synthetic intake set."""
import csv
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SPLITS={'MTS-Dialog-TrainingSet.csv':'train','MTS-Dialog-ValidationSet.csv':'validation',
        'MTS-Dialog-TestSet-1-MEDIQA-Chat-2023.csv':'test_1','MTS-Dialog-TestSet-2-MEDIQA-Sum-2023.csv':'test_2'}
REQUIRED=('age','symptoms','medication_review','allergy_review','renal_function','lvef_review')


def build():
    folder=ROOT/'datasets/mts_dialog';source=json.loads((folder/'source_commit.json').read_text())
    cases=[];files=[];seen={};duplicates=[]
    for filename,split in SPLITS.items():
        path=folder/'raw'/filename;raw=path.read_bytes()
        record=next(x for x in source['files'] if x['file']==filename)
        digest=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        if len(raw)!=record['bytes'] or digest!=record['git_blob_sha']:raise ValueError('Upstream bytes differ: '+filename)
        with path.open(encoding='utf-8-sig',newline='') as source_file:
            rows=list(csv.DictReader(source_file))
        for i,row in enumerate(rows):
            case={'id':f'mts-{split}-{i:04d}','split':split,'language':'en','title':row['dialogue'].splitlines()[0][:90],
                  'dialogue':row['dialogue'],'gold':{'section':row['section_header'].lower(),'note':row['section_text']},
                  'source_role':'public_research_dialogue','paired_imaging_case_id':None,
                  'source':{'repository':source['repository'],'commit':source['commit'],'file':filename,'row_index':i,'upstream_id':row['ID']},'intake_form':None}
            key=hashlib.sha256(row['dialogue'].strip().encode()).hexdigest()
            if key in seen:duplicates.append({'first':seen[key],'repeat':case['id'],'cross_split':seen[key].split('-')[1]!=split})
            seen[key]=case['id'];cases.append(case)
        files.append({'file':filename,'rows':len(rows),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'git_blob_sha':digest})
    titles=['完整交接','缺少藥單','缺少過敏資料','缺少腎功能資料','缺少心功能資料','多項缺漏','急性旗標優先','尚未確認急性狀態','不同來源不可合併','未核對數字','醫師備註待確認','來源追溯彩排']
    missing=[[],['medication_review'],['allergy_review'],['renal_function'],['lvef_review'],['medication_review','allergy_review','renal_function'],[],['acute'],[],['lvef_review'],['medication_review'],[]]
    for i,title in enumerate(titles):
        form={'age':60+i,'symptoms':'胸部不適的合成敘述，尚未作臨床判讀','medication_review':'已提供人工藥單核對紀錄','allergy_review':'已提供人工過敏核對紀錄','renal_function':'已提供腎功能報告來源','lvef_review':'已提供正式心功能報告來源','acute':i==6}
        if i==7:form['acute']=None
        for key in missing[i]:form[key]=None
        lines=['來源：這是人工設計的合成流程案例，不對應病人或內附 MRI。',f'資料員：本次案例代碼 SYNTH-{i+1:02}，年齡欄位 {form["age"]} 歲。',
               '資料員：主訴欄位為「'+form['symptoms']+'」。']
        labels={'medication_review':'用藥核對','allergy_review':'過敏核對','renal_function':'腎功能來源','lvef_review':'心功能來源'}
        for key,label in labels.items():lines.append('資料員：'+label+'：'+(form[key] or '尚未提供，請人員補件')+'。')
        lines.append('人員確認：急性警訊旗標為'+('是' if form['acute'] is True else '未確認' if form['acute'] is None else '否')+'；此旗標不由模型推斷。')
        cases.append({'id':f'synthetic-{i+1:02}','split':'synthetic_demo','language':'zh-Hant','title':title,'dialogue':'\n'.join(lines),
                      'source_role':'synthetic_workflow_fixture','source':{'author':'CardioAgent project','version':'2.2.0'},'intake_form':form,
                      'gold':{'section':None,'note':None,'expected_missing':missing[i]},'paired_imaging_case_id':None})
    output=folder/'cases.jsonl'
    output.write_bytes(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in cases).encode('utf-8'))
    manifest={'schema':'cardio-aipc-data/1','retrieved_date':'2026-11-03','source_commit':source['commit'],'license':'CC-BY-4.0',
              'citation':'Ben Abacha et al. (2023), An Empirical Study of Clinical Note Generation from Doctor-Patient Encounters, EACL, 2291-2302.',
              'citation_url':'https://aclanthology.org/2023.eacl-main.168/','files':files,'public_dialogues':1701,'synthetic_cases':12,
              'cases_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'normalized_dialogue_duplicates':duplicates,
              'patient_independence_verified':False,'cross_modal_pairing_available':False,'gold_not_in_model_input':True,
              'notice':'原始官方切分保留；只作研究文字與部署驗證，不是中文心臟專科臨床驗證。MRI與文字沒有病人配對。'}
    (folder/'manifest.json').write_bytes((json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    return manifest


if __name__=='__main__':
    d=build();print(json.dumps({'public_dialogues':d['public_dialogues'],'synthetic_cases':d['synthetic_cases'],'splits':{x['file']:x['rows'] for x in d['files']},'duplicate_dialogues':len(d['normalized_dialogue_duplicates'])},ensure_ascii=False))
