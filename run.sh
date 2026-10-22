#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    echo "[media-lab] creating virtualenv..."
    python3 -m venv venv
    ./venv/bin/pip install --quiet --upgrade pip
    ./venv/bin/pip install --quiet -r requirements.txt
fi

echo "[media-lab] starting Flask on http://0.0.0.0:3000 ..."
exec ./venv/bin/python app.py