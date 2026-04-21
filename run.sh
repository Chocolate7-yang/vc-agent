#!/usr/bin/env bash
# 一键启动（项目根目录执行）——仅两种模式：
#   ./start.sh | bash run.sh | bash run.sh start   收料 → 每日简报 → 飞书推送
#   ./start.sh 7x24 | bash run.sh 7x24             每 4h 收料、每日 7:00 晨间简报（含飞书）
# 详细日志：VC_AGENT_QUIET=0 bash run.sh …

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

export ALLOW_INSECURE_SSL="${ALLOW_INSECURE_SSL:-true}"
export PIP_DISABLE_PIP_VERSION_CHECK=1

usage() {
  cat <<'EOF'
用法: bash run.sh [命令]

  （默认）start | all    ① 收料（pipeline）② 晨间简报（daily_brief）③ 飞书推送
  7x24 | stack           无人值守：每 PIPELINE_INTERVAL_HOURS 小时收料、每日 BRIEF_HOUR 晨间简报并推飞书（前台常驻调度器）
  test724 HH:MM          7x24 测试模式：临时设置 TEST_BRIEF_HOUR/MINUTE（无需改 .env）

  doctor | self-check    环境与链路自检（配置/网络/DB/简报）
  feishu-ws | events     仅启动飞书卡片回调长连接（已加载 .env；前台日志，Ctrl+C 退出）
  help                   本说明

环境变量（节选）:
  VC_AGENT_QUIET         默认 1；0 为详细日志（一键模式将不显示引导心跳行）
  PIPELINE_INTERVAL_HOURS  默认 4（仅 7x24）
  BRIEF_HOUR / BRIEF_MINUTE / BRIEF_TZ  晨间简报时刻（默认 7:00 北京时间）
  TEST_BRIEF_HOUR / TEST_BRIEF_MINUTE   可选测试时刻；设置后会额外触发一次简报+飞书（默认关闭，不影响正式时刻）
  FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_RECEIVE_ID  飞书应用发消息必需项
  FEISHU_RECEIVE_ID_TYPE  默认 chat_id
  已配置 FEISHU_APP_ID/SECRET 时：7x24/自检等在脚本内后台拉起 feishu_events；一键 start 则在「飞书推送卡片」前由 Python 拉起（脱离 shell，日志仍见 logs/feishu_events.log）
  FEISHU_WS_RESTART_SEC   feishu_events 断线重连间隔（秒），默认 5
  RUN_7X24_BOOTSTRAP     默认 1：7x24 启动前先同步跑一轮收料+简报；0 则交给调度器
EOF
}

MODE="${1:-start}"
TIME_ARG="${2:-}"
case "$MODE" in
  -h|--help|help)
    usage
    exit 0
    ;;
esac

case "$MODE" in
  start|all|""|7x24|stack|unattended|daemon|doctor|self-check|test724)
    export VC_AGENT_QUIET="${VC_AGENT_QUIET:-1}"
    ;;
  *)
    export VC_AGENT_QUIET="${VC_AGENT_QUIET:-0}"
    ;;
esac

ensure_venv_and_deps() {
  local py="python3"
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    py="$ROOT/.venv/bin/python"
  elif [[ ! -d "$ROOT/.venv" ]]; then
    echo "📦 创建虚拟环境 .venv …"
    "$py" -m venv "$ROOT/.venv"
    py="$ROOT/.venv/bin/python"
  fi
  "$py" -m pip install -q -r "$ROOT/requirements.txt"
  echo "✅ 运行环境就绪 · $($py -c 'import sys; print(sys.version.split()[0])') · $(basename "$py")"
}

ensure_venv_and_deps
PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

mkdir -p "$ROOT/logs"
# 仅 7x24/doctor 等需要尽早后台长连接；一键 start 在简报前拉起（见下）；feishu-ws 为前台独占
_feishu_ws_running() {
  pgrep -f "vc_agent\.feishu_events" >/dev/null 2>&1 || pgrep -f "[f]eishu_events" >/dev/null 2>&1
}
# 一键收料可能很久，过早建 WSS 易空闲断线，飞书端会报「回调服务未在线」
_feishu_ws_defer_until_daily_brief() {
  case "$MODE" in
    start|all|"") return 0 ;;
    *) return 1 ;;
  esac
}
_launch_feishu_ws_bg_if_configured() {
  if [[ "$MODE" == "feishu-ws" || "$MODE" == "events" ]]; then
    return 0
  fi
  if [[ -n "${FEISHU_APP_ID:-}" && -n "${FEISHU_APP_SECRET:-}" ]]; then
    if ! _feishu_ws_running; then
      echo "📡 后台启动 feishu_events（飞书卡片 👍/👎 长连接）…"
      # 必须在当前 shell 里 nohup &，再 disown；勿用 ( subshell & )，
      # 否则子 shell 退出时可能向后台发 SIGHUP，进程秒退（手动前台跑则正常）。
      pushd "$ROOT" >/dev/null || exit 1
      export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
      nohup "$PY" -m vc_agent.feishu_events >>"$ROOT/logs/feishu_events.log" 2>&1 &
      _feishu_pid=$!
      # 从 job 表移除，避免非交互脚本结束时仍向该作业发 SIGHUP（nohup 子进程已忽略 SIGHUP，双保险）
      disown "$_feishu_pid" 2>/dev/null || true
      popd >/dev/null || true
      sleep 3
      if _feishu_ws_running; then
        echo "   ✅ 已启动 · 日志: $ROOT/logs/feishu_events.log"
      else
        echo "   ⚠️  feishu_events 未能保持运行（卡片会提示「回调服务未在线」）" >&2
        echo "   请查看: $ROOT/logs/feishu_events.log" >&2
        if [[ -f "$ROOT/logs/feishu_events.log" ]]; then
          echo "   --- 日志末尾 ---" >&2
          tail -n 30 "$ROOT/logs/feishu_events.log" | sed 's/^/   /' >&2
        fi
      fi
    else
      echo "📡 feishu_events 已在运行，跳过"
    fi
  else
    if [[ -n "${FEISHU_APP_ID:-}" || -n "${FEISHU_APP_SECRET:-}" ]]; then
      echo "⚠️  飞书凭证不完整（需同时配置 FEISHU_APP_ID 与 FEISHU_APP_SECRET），未启动 feishu_events" >&2
    else
      echo "ℹ️  未配置 FEISHU_APP_ID/FEISHU_APP_SECRET，未启动 feishu_events（仅推送简报、卡片按钮无回调）" >&2
    fi
  fi
}
if [[ "$MODE" != "feishu-ws" && "$MODE" != "events" ]]; then
  if _feishu_ws_defer_until_daily_brief; then
    echo "ℹ️  一键模式：飞书长连接将在「推送卡片」前由 Python 拉起（脱离 shell，避免 bash 结束后回调进程退出）"
  else
    _launch_feishu_ws_bg_if_configured
  fi
fi

case "$MODE" in
  start|all|""|7x24|stack|unattended|daemon|doctor|self-check|test724|feishu-ws|events) ;;
  *)
    echo "未知命令: $MODE（支持 start / 7x24 / test724 / feishu-ws / doctor）" >&2
    usage >&2
    exit 1
    ;;
esac

if [[ "$MODE" == "test724" ]]; then
  if [[ -z "$TIME_ARG" ]]; then
    echo "test724 需要时间参数，例如：bash run.sh test724 19:05" >&2
    exit 1
  fi
  if [[ ! "$TIME_ARG" =~ ^([01]?[0-9]|2[0-3]):([0-5][0-9])$ ]]; then
    echo "时间格式错误：$TIME_ARG（应为 HH:MM，例如 19:05）" >&2
    exit 1
  fi
  export TEST_BRIEF_HOUR="${BASH_REMATCH[1]}"
  export TEST_BRIEF_MINUTE="${BASH_REMATCH[2]}"
  MODE="7x24"
fi

run_with_bootstrap_progress() {
  local label="$1"
  shift
  if [[ "${VC_AGENT_QUIET:-1}" == "0" ]]; then
    printf '   %s\n' "$label"
    "$@"
    return $?
  fi
  local _hbpid
  (
    local _n=0
    while sleep 12; do
      ((_n += 12)) || true
      printf '   … %s 仍在运行（已约 %s 秒，Ctrl+C 可中断）\n' "$label" "$_n" >&2
    done
  ) &
  _hbpid=$!
  _bootstrap_hb_stop() {
    kill "$_hbpid" 2>/dev/null || true
    wait "$_hbpid" 2>/dev/null || true
  }
  trap '_bootstrap_hb_stop' INT
  printf '   %s 开始…（Ctrl+C 可中断）\n' "$label" >&2
  "$@"
  local _ec=$?
  trap - INT
  _bootstrap_hb_stop
  return "$_ec"
}

case "$MODE" in
  feishu-ws|events)
    echo ""
    echo "  VC Agent · 飞书卡片回调（长连接，前台）"
    echo "  ─────────────────────────────────────────"
    echo "  已加载 $ROOT/.env · PYTHONPATH 已指向 src"
    exec "$PY" -m vc_agent.feishu_events
    ;;
  doctor|self-check)
    echo ""
    echo "  VC Agent · 自检模式"
    echo "  ───────────────────"
    "$PY" - <<'PY'
import json
import os
import sqlite3
import ssl
import subprocess
from pathlib import Path
from urllib import request

root = Path.cwd()
data = root / "data"
db = data / "vc_agent.db"
brief = root / "src" / "vc_agent" / "data" / "brief_latest.json"
log_feishu = root / "logs" / "feishu_events.log"
print(f"[CHECK] PYTHONPATH={os.getenv('PYTHONPATH','')}")
print(f"[CHECK] LLM key configured={'yes' if (os.getenv('QWEN_KEY') or os.getenv('OPENAI_API_KEY')) else 'no'}")
if os.getenv("FEISHU_APP_ID") and os.getenv("FEISHU_APP_SECRET"):
    r = subprocess.run(
        ["pgrep", "-f", "vc_agent.feishu_events|feishu_events.py"],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        print("[CHECK] feishu_events: process running (card callback WSS)")
    else:
        print("[WARN] feishu_events: not running — 卡片 👍/👎 会提示「回调服务未在线」；请执行: bash run.sh feishu-ws（或 bash run.sh start 会自动后台拉起）")
    if log_feishu.is_file():
        try:
            tail = log_feishu.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
            print("[CHECK] feishu_events.log (last lines):")
            for line in tail:
                print("       ", line[:200])
        except OSError as exc:
            print(f"[WARN] feishu_events.log read fail: {exc}")
else:
    print("[CHECK] feishu_events: skipped (no FEISHU_APP_ID/FEISHU_APP_SECRET)")
try:
    insecure = str(os.getenv("ALLOW_INSECURE_SSL", "")).lower() in {"1", "true", "yes", "on"}
    ctx = ssl._create_unverified_context() if insecure else ssl.create_default_context()
    request.urlopen("https://www.youtube.com", timeout=8, context=ctx)
    print("[CHECK] network youtube=ok")
except Exception as exc:
    print(f"[WARN] network youtube=fail ({exc})")
if db.exists():
    try:
        with sqlite3.connect(db) as conn:
            n = conn.execute("select count(*) from briefs").fetchone()[0]
        print(f"[CHECK] sqlite briefs={n}")
    except Exception as exc:
        print(f"[WARN] sqlite error ({exc})")
else:
    print("[WARN] sqlite missing (data/vc_agent.db)")
if brief.exists():
    try:
        payload = json.loads(brief.read_text(encoding="utf-8"))
        print(f"[CHECK] latest brief id={payload.get('brief_id','')}")
    except Exception as exc:
        print(f"[WARN] brief_latest.json invalid ({exc})")
else:
    print("[WARN] brief_latest.json missing")
PY
    ;;
  start|all|"")
    echo ""
    echo "  VC Agent · 一键：收料 → 简报 → 飞书"
    echo "  ─────────────────────────────────────"
    run_with_bootstrap_progress "1/2 收料 pipeline …" \
      "$PY" -c "from vc_agent.pipeline_service import run_pipeline; run_pipeline()"
    run_with_bootstrap_progress "2/2 晨间简报 daily_brief（含飞书）…" \
      "$PY" -c "from vc_agent.pipeline_service import run_daily_brief; run_daily_brief()"
    echo "✅ 简报与飞书已完成"
    ;;
  7x24|stack|unattended|daemon)
    _ph="${PIPELINE_INTERVAL_HOURS:-4}"
    _bh="${BRIEF_HOUR:-7}"
    _bm=$(printf '%02d' "$((10#${BRIEF_MINUTE:-0}))")
    _tbh="${TEST_BRIEF_HOUR:-}"
    _tbm=$(printf '%02d' "$((10#${TEST_BRIEF_MINUTE:-0}))")
    echo ""
    echo "  VC Agent · 7×24（每 ${_ph}h 收料 · 每日 ${_bh}:${_bm} 简报+飞书）"
    echo "  ─────────────────────────────────────────────"
    if [[ -n "${_tbh}" ]]; then
      echo "   · 测试时刻：每日 ${_tbh}:${_tbm} 额外简报+飞书（不影响正式时刻）"
    fi
    if [[ "${RUN_7X24_BOOTSTRAP:-1}" != "0" ]]; then
      echo "   · 启动引导：收料 → 晨间简报（首次简报不推飞书，避免与定时重复）"
      run_with_bootstrap_progress "1/2 收料 pipeline …" \
        "$PY" -c "from vc_agent.pipeline_service import run_pipeline; run_pipeline()"
      run_with_bootstrap_progress "2/2 晨间简报 daily_brief …" \
        env VC_AGENT_BOOTSTRAP_BRIEF=1 "$PY" -c "from vc_agent.pipeline_service import run_daily_brief; run_daily_brief()"
      echo "   ✅ 引导完成"
      echo ""
      export RUN_PIPELINE_ON_START=0
      export RUN_DAILY_ON_START=0
    else
      export RUN_PIPELINE_ON_START="${RUN_PIPELINE_ON_START:-1}"
      export RUN_DAILY_ON_START="${RUN_DAILY_ON_START:-1}"
      echo "   · RUN_7X24_BOOTSTRAP=0：收料/简报由调度器执行"
      echo ""
    fi
    echo "✅ 调度即将启动 · 每 ${_ph}h 收料 · 每日 ${_bh}:${_bm} 简报 (${BRIEF_TZ:-Asia/Shanghai})"
    if [[ -n "${_tbh}" ]]; then
      echo "✅ 测试简报时刻：每日 ${_tbh}:${_tbm} (${BRIEF_TZ:-Asia/Shanghai})"
    fi
    exec "$PY" -m vc_agent.scheduler
    ;;
esac
