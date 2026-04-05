#!/bin/bash

set -u

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ROOT_DIR/logs"
LAUNCH_LOG="$LOG_DIR/launcher.log"
VENV_DIR="$ROOT_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"

mkdir -p "$LOG_DIR"

timestamp() {
  date +"%Y-%m-%d %H:%M:%S"
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$1" | tee -a "$LAUNCH_LOG"
}

version_supported() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)
PY
}

find_python() {
  local candidates=(
    "${PYTHON_BIN:-}"
    "/opt/homebrew/bin/python3.13"
    "/usr/local/bin/python3.13"
    "python3.13"
    "/opt/homebrew/bin/python3.12"
    "/usr/local/bin/python3.12"
    "python3.12"
    "/opt/homebrew/bin/python3.11"
    "/usr/local/bin/python3.11"
    "python3.11"
    "python3"
  )
  local candidate
  local resolved

  for candidate in "${candidates[@]}"; do
    [ -n "$candidate" ] || continue
    resolved=""
    if command -v "$candidate" >/dev/null 2>&1; then
      resolved="$(command -v "$candidate")"
    elif [ -x "$candidate" ]; then
      resolved="$candidate"
    fi
    [ -n "$resolved" ] || continue
    if version_supported "$resolved"; then
      printf '%s\n' "$resolved"
      return 0
    fi
  done
  return 1
}

ensure_venv() {
  local python_bin="$1"

  if [ -x "$VENV_PY" ] && version_supported "$VENV_PY"; then
    return 0
  fi

  log "正在创建虚拟环境..."
  rm -rf "$VENV_DIR"
  if ! "$python_bin" -m venv "$VENV_DIR" >>"$LAUNCH_LOG" 2>&1; then
    log "创建虚拟环境失败。"
    return 1
  fi
}

main() {
  local python_bin

  if ! python_bin="$(find_python)"; then
    log "未找到 Python 3.11+。"
    echo
    echo "请先安装 Python 3.11 或更高版本，然后重新运行 start.command。"
    read -r -p "按回车键退出..." _
    return 1
  fi

  if ! ensure_venv "$python_bin"; then
    echo
    echo "启动失败，请查看日志：$LAUNCH_LOG"
    read -r -p "按回车键退出..." _
    return 1
  fi

  log "使用解释器：$VENV_PY"
  "$VENV_PY" "$ROOT_DIR/run.py" >>"$LAUNCH_LOG" 2>&1
  local exit_code=$?
  if [ "$exit_code" -ne 0 ]; then
    log "程序退出，状态码：$exit_code"
    echo
    echo "启动失败，请查看日志：$LAUNCH_LOG"
    read -r -p "按回车键退出..." _
    return "$exit_code"
  fi
  return 0
}

main "$@"
