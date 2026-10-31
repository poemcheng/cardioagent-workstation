"""Run real HTTP with authenticated hospital workflow and DOM integration."""
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from waitress import create_server
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cardio.app import create_app

if __name__=='__main__':
    with tempfile.TemporaryDirectory() as td:
        app=create_app(td);server=create_server(app,host='127.0.0.1',port=0,threads=4)
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        try:
            result=subprocess.run([os.environ.get('CODEX_PRIMARY_RUNTIME_NODE','node'),str(ROOT/'scripts/dom_smoke.cjs'),'http://127.0.0.1:'+str(server.effective_port)],cwd=ROOT,timeout=45)
        finally:
            server.close();app.pool.shutdown(wait=True)
        raise SystemExit(result.returncode)
