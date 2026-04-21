#!/usr/bin/env bash
# 一键启动入口：等同于 bash run.sh（默认 start）
#   ./start.sh          → 收料 → 简报 → 飞书
#   ./start.sh 7x24     → 定时收料 + 定时报简报（见 run.sh --help）
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$ROOT/run.sh" "${@:-start}"
