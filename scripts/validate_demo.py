"""Run software checks against disposable local data; no AMD/model claims."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dom', action='store_true', help='Also run Node/jsdom real HTTP integration')
    parser.add_argument('--node-modules', type=Path, help='Folder containing jsdom')
    args = parser.parse_args()
    evidence = ROOT / 'evidence'
    evidence.mkdir(exist_ok=True)
    unit = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                          cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    (evidence / 'unit_tests.txt').write_text(unit.stdout + unit.stderr, encoding='utf-8')
    (evidence / 'integrated_unit_tests.txt').write_text(unit.stdout + unit.stderr, encoding='utf-8')
    print(unit.stdout + unit.stderr, end='')
    if unit.returncode:
        return unit.returncode
    if not args.dom:
        return 0
    node = shutil.which('node')
    if not node:
        print('Node and jsdom are required for --dom; they are not needed to run the workstation.', file=sys.stderr)
        return 2
    env = os.environ.copy()
    if args.node_modules:
        env['NODE_PATH'] = str(args.node_modules.resolve())
    for script in ('competition_dom_smoke.cjs', 'integrated_dom_smoke.cjs'):
        code = run_dom(node, env, evidence, script)
        if code:
            return code
    mesh = subprocess.run([node, 'scripts/mesh_viewer_check.cjs'], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding='utf-8')
    (evidence/'mesh_viewer_checks.txt').write_text(mesh.stdout+mesh.stderr, encoding='utf-8')
    print(mesh.stdout+mesh.stderr, end='')
    return mesh.returncode


def run_dom(node, env, evidence, script):
    prefix = 'aipc_browser' if script.startswith('aipc_') else 'integrated_dom' if script.startswith('integrated') else 'competition_dom'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='cardio-validation-') as data:
        with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable, 'run_demo.py', '--port', str(port), '--data-dir', data],
                                       cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, text=True)
            try:
                base = f'http://127.0.0.1:{port}'
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                deadline = time.monotonic() + 20
                while True:
                    try:
                        with opener.open(base + '/api/health', timeout=1) as response:
                            if json.load(response)['status'] == 'ok':
                                break
                    except Exception:
                        if process.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError('Disposable validation server did not start')
                        time.sleep(.1)
                result = subprocess.run([node, 'scripts/'+script, base],
                                        cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8')
                (evidence / (prefix+'_log.txt')).write_text(result.stdout + result.stderr, encoding='utf-8')
                print(result.stdout + result.stderr, end='')
                return result.returncode
            finally:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                log.seek(0)
                (evidence / (prefix+'_server_log.txt')).write_text(log.read(), encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
