#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
if [[ ! -x .venv/bin/uvicorn ]]; then
  echo "Create the environment first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
.venv/bin/python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8001
