# Source in the terminal used for evaluation. Keep credentials in the shell.
: "${DCC_WORK_ROOT:?Set an artifact directory outside the checkout}"
export DATA_ROOT="$DCC_WORK_ROOT/dcc-data"
export DCC_RUN_ROOT="$DCC_WORK_ROOT/dcc-runs"
export DCC_MODEL_NAME=deepseek-flash
export DCC_MODEL_BASE_URL=https://api.deepseek.com
export DCC_JUDGE_MODEL=Qwen3-8B
export DCC_JUDGE_BASE_URL=http://127.0.0.1:18001/v1
export DCC_JUDGE_API_KEY=EMPTY
export DCC_SEARCH_BACKEND=public_web
export no_proxy="localhost,127.0.0.1,::1${no_proxy:+,$no_proxy}${NO_PROXY:+,$NO_PROXY}"
export NO_PROXY="$no_proxy"
