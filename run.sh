#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python -m streamlit run smartsehat_workspace/app.py --server.address 0.0.0.0
else
  exec python -m streamlit run smartsehat_workspace/app.py --server.address 0.0.0.0
fi
