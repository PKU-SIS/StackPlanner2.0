#!/usr/bin/env bash
set -euo pipefail
cd /data2/pkuiti/zmy/codex-relay-work/outline-self-budget-newcase33-20261117
exec env -u http_proxy -u https_proxy -u all_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY PYTHONDONTWRITEBYTECODE=1 /data2/pkuiti/zmy/projects/reviews/StackPlanner2-sp2DeepResearch-20261103/backend/.venv/bin/python -B run.py --start
