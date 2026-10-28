# 独立 DCC Harness：评测与发布

`dcc_harness/` 是数据集适配与评测包，支持 WideSearch 和 CUAD。它直接调用原生 SP Central 和子 agent，不依赖 SLIME、Ray、SGLang 或 Megatron。
训练仓库中的旧模块路径只做兼容转发，训练通过同一份数据、工具和 scorer 工作，不再维护另一份实现。
`prepare_training_rows`、训练会话、轨迹、奖励接入和参数更新仍属于 RL 插件，不导出到 SP 仓库。

支持两种源码布局：当前训练仓库的 `StackPlanner2.0/backend/packages/harness/`，以及 SP 仓库的 `backend/packages/harness/`。
源码重新组织后使用新运行目录，不能续跑旧 manifest；旧结果不要删除。
本次仅整理模块位置及依赖，不修改 SP 核心、提示词、验收逻辑、答案解析和评分规则，不代表已修复 coder 验收问题。

## 在 Linux 评测

进入已安装 SP 运行时依赖的 Python 3.12 环境。新环境可在 SP 仓库中安装：

```bash
python -m pip install -e backend/packages/harness
python -m pip install -r configs/dcc/requirements.txt
```

这不是 SLIME 训练环境安装命令；已有 `dcc-harness` 环境不必重复安装。
从代码仓库根目录执行，`DCC_WORK_ROOT` 指向代码外的现有工作目录：

```bash
export DCC_WORK_ROOT=/data/pkuiti/zym
source configs/dcc/harness.example.sh
read -rsp 'DeepSeek API key: ' DCC_MODEL_API_KEY
export DCC_MODEL_API_KEY
echo
curl --fail "$DCC_JUDGE_BASE_URL/models"
python -B scripts/evaluate_sp_benchmarks.py --check-config
python -B scripts/evaluate_sp_benchmarks.py --check-search
RUN_DIR="$DCC_RUN_ROOT/$(date +%Y%m%d-%H%M%S)-deepseek-dev3"
python -u scripts/evaluate_sp_benchmarks.py \
  --tasks "$DATA_ROOT/web-curriculum-v3/dev3.jsonl" \
  --dataset-source independent-curriculum --run-dir "$RUN_DIR" \
  --thinking disabled --max-actions 20 --max-tokens 4096 --timeout 900 --seed 0
```

需要已有的 Qwen3-8B OpenAI 兼容 judger 服务；这里不启动或抢占 GPU。权重版本已知时增加 `--judge-revision <版本或指纹>`，未知时报告 `unknown`。
API 模型 ID 以服务端实际提供的 ID 为准。代理由当前终端设置；代理可达不保证 DuckDuckGo 不触发验证码。
将 `--tasks` 改为 `validation.jsonl` 可运行独立 34 题。它不是官方 WideSearch 测试集。
正式官方评测使用 `--tasks "$DATA_ROOT/widesearch/test.jsonl" --dataset-source widesearch-official`，要求完整 200 题；调试期间不以官方测试答案调整提示词。
运行目录保存 `manifest.json`、`predictions.jsonl`、`report.json` 和原生轨迹。Qwen judger 为 custom judge，不能称为原始官方 judge 成绩。

## 创建并推送 DCCv2 分支

在 Linux 服务器的现有 `dcc-harness` 环境运行。下面创建新的 SP 仓库副本，不切换原训练仓库的分支。
目标目录必须不存在；远端 `DCCv2` 分支若已经存在，应先核查，不能强推覆盖。

```bash
(
set -euo pipefail
SOURCE_REPO=/data/pkuiti/zym/StackPlanner-DCC
PUBLISH_REPO=/data/pkuiti/zym/StackPlanner2-DCCv2
test ! -e "$PUBLISH_REPO"
remote_branch=$(git ls-remote --heads https://github.com/PKU-SIS/StackPlanner2.0.git refs/heads/DCCv2)
test -z "$remote_branch"
git clone --branch main --single-branch https://github.com/PKU-SIS/StackPlanner2.0.git "$PUBLISH_REPO"
cd "$PUBLISH_REPO"
git switch -c DCCv2
python "$SOURCE_REPO/scripts/export_dcc_harness.py" --destination "$PUBLISH_REPO"
python -B scripts/evaluate_sp_benchmarks.py --check-config
python -m pytest tests/test_sp_benchmarks.py tests/test_sp_dataset_plugins.py -q
git --no-pager diff --check
git add -- dcc_harness scripts/evaluate_sp_benchmarks.py scripts/dcc_prepare_data.py \
  scripts/dcc_prepare_web_curriculum.py configs/stackplanner_benchmarks.yaml \
  configs/stackplanner_skills configs/dcc/requirements.txt configs/dcc/harness.example.sh \
  docs/dcc_harness.md tests/conftest.py tests/test_sp_benchmarks.py tests/test_sp_dataset_plugins.py
git --no-pager diff --cached --check
git --no-pager diff --cached --stat
git status --short
)
```

检查暂存清单后执行：

```bash
cd /data/pkuiti/zym/StackPlanner2-DCCv2
git --no-pager diff --cached
git commit -m "Add native SP benchmark adapters for WideSearch and CUAD"
git push -u origin DCCv2
```

需要对 `PKU-SIS/StackPlanner2.0` 的写权限；认证失败时配置自己的 GitHub SSH/凭据，不要将 token 写进 URL、脚本或聊天。
发布从运行命令时的最新 `main` 开始；如果它已更新，必须以新 checkout 的验证结果为准。不要改为用旧 `DCC` 分支覆盖当前 SP 核心。
导出只包含适配包、配置/Skill、数据准备和评测入口、相关测试及本页；不复制嵌套 SP、SLIME、训练启动器、模型、数据、日志或私人配置。
