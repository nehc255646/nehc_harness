#!/usr/bin/env bash
# M5 端到端验收：审批 → 写文件 diff → 重连规则/pending
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
echo "[demo] 跑集成验收（heuristic 演示模式，无需模型 key）..."
uv run pytest -q tests/integration/test_ws.py tests/unit/test_files.py tests/unit/test_shell_progress.py
echo
echo "[demo] 通过。启动 UI：仓库根目录 ./start.sh"
echo "  1. 顶栏可切换强调色"
echo "  2. 发送「执行 echo hello」→ 三选审批 → 同类放行"
echo "  3. 发送「写入 hello.txt」→ 工具卡展示 diff"
echo "  4. 刷新页面：历史与放行规则仍在"
echo "  5. 侧栏双击可重命名会话"
