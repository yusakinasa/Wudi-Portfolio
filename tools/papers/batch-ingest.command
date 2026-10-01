#!/bin/zsh
# Finder launcher for this installation. Never use the Desktop as the data root.
set -u
PROJECT_DIR="/Users/wudi/code/Portfolio"
PYTHON_BIN="$PROJECT_DIR/.venv-papers/bin/python"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
export CODEX_HOME="${CODEX_HOME:-$HOME/.codex-personal}"

main() {
    if [[ ! -x "$PYTHON_BIN" || ! -f "$PROJECT_DIR/tools/papers/batch.py" ]]; then
        print -u2 "找不到项目或 .venv-papers：$PROJECT_DIR。请先完成论文工具安装。"
        return 1
    fi
    if [[ ! -d "$CODEX_HOME" ]]; then
        print -u2 "找不到 Codex 配置目录：$CODEX_HOME。请检查 CODEX_HOME。"
        return 1
    fi
    cd "$PROJECT_DIR" || return 1
    print "Codex 配置目录：$CODEX_HOME"
    print "逐篇独立分析；Ctrl+C 停止，失败原文保留，可重跑。"
    "$PYTHON_BIN" -u "$PROJECT_DIR/tools/papers/batch.py" \
        --new "$HOME/paper_reading/new" --ready "$HOME/paper_reading/ready" \
        --model gpt-6.1-sol --reasoning-effort high
}

main
batch_status=$?
if [[ -t 0 ]]; then
    print ""
    read -r "batch_reply?按回车关闭窗口… "
fi
exit "$batch_status"
