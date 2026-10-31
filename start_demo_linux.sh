#!/usr/bin/env sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ -x .venv/bin/python ]; then
    demo_python=.venv/bin/python
else
    demo_python=python3
fi
if ! "$demo_python" -c 'import flask, waitress, numpy, PIL, pydicom' >/dev/null 2>&1; then
    echo 'Install the Linux dependencies first: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt' >&2
    echo 'Bundled binary wheels target Windows CPython 3.11; no automatic Linux download is performed.' >&2
    exit 1
fi
"$demo_python" scripts/prepare_demo_assets.py
"$demo_python" scripts/demo_preflight.py --output evidence/demo_preflight.json
exec "$demo_python" run_demo.py --open-browser "$@"
