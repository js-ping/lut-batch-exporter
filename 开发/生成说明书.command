#!/bin/zsh
# 改完 开发/使用说明.md 后双击这个，重新生成 使用说明.html 和 程序/static/help.html
cd "$(dirname "$0")" || exit 1
cd "$(dirname "$0")/.." && /usr/bin/python3 "开发/build_help.py" || {
  osascript -e 'display dialog "生成失败，请检查 开发/使用说明.md 是否存在" buttons {"好"} default button 1 with icon stop'
  exit 1
}
open "使用说明.html"
