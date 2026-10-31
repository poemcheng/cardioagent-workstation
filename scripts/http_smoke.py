"""Exercise actual HTTP, cookie/CSRF, worker queue and human approval locally."""
import http.cookiejar
import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from waitress import create_server
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cardio.app import create_app

if __name__=="__main__":
    with tempfile.TemporaryDirectory() as td:
        app=create_app(td,access_control=False);server=create_server(app,host='127.0.0.1',port=0,threads=4)
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.effective_port)
        client=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        def get(route):
            with client.open(base+route,timeout=5) as res:return json.load(res)
        token=get('/api/session')['csrf']
        def post(route,body):
            req=urllib.request.Request(base+route,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-CSRF-Token':token})
            with client.open(req,timeout=5) as res:return json.load(res)
        with client.open(base,timeout=5) as res:assert res.status==200 and b'worklist' in res.read()
        with client.open(base+'/static/app.js',timeout=5) as res:assert res.status==200
        result=post('/api/runs',{'case_id':'demo-ct-positive','center':1,'planner':'rules','mode':'demo','context':{'age':62,'acute':False,'history_complete':True,'consent':True}})
        deadline=time.monotonic()+15
        while True:
            run=get('/api/runs/'+result['id'])
            if run['status'] not in {'queued','running'}:break
            if time.monotonic()>deadline:raise RuntimeError('worker timeout')
            time.sleep(.05)
        assert run['status']=='pending_review' and len(run['vision']['candidates'])==2 and not run['tasks']
        post('/api/runs/'+run['id']+'/review',{'decision':'approve','reviewer':'HTTP synthetic validation','version':run['version']})
        run=get('/api/runs/'+run['id']);assert len(run['tasks'])==2
        exported=get('/api/runs/'+run['id']+'/export');assert exported['audit']['valid']
        report={'passed':True,'transport':'real HTTP / Waitress loopback','synthetic_fixture':True,'planner':'rules','tools':run['completed'],'tasks_before_review':0,'tasks_after_review':len(run['tasks']),'candidates':len(run['vision']['candidates']),'audit':exported['audit'],'browser_verified':False,'hardware_verified':False}
        (ROOT/'evidence/http_smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        # Export only this synthetic fixture, never a user's real run.
        (ROOT/'evidence/synthetic_workflow_evidence.json').write_text(json.dumps(exported,ensure_ascii=False,indent=2),encoding='utf-8')
        server.close();app.pool.shutdown(wait=True)
        print(json.dumps(report,ensure_ascii=False,indent=2))
