#!/bin/zsh
# ============================================================
#  LUT 批量导出器 · 双击启动
#  关闭弹出的终端窗口 = 退出工具
# ============================================================
cd "$(dirname "$0")" || exit 1

PY=""
for c in /usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 $(command -v python3 2>/dev/null); do
  if [ -x "$c" ]; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
  osascript -e 'display dialog "没有找到 Python 3，工具无法启动。\n\n修复方法：打开「终端」，执行\nxcode-select --install\n装完再双击本文件。" buttons {"好"} default button 1 with icon stop'
  exit 1
fi

FF=""
for c in /opt/homebrew/bin/ffmpeg /usr/local/bin/ffmpeg $(command -v ffmpeg 2>/dev/null); do
  if [ -x "$c" ]; then FF="$c"; break; fi
done

if [ -z "$FF" ]; then
  osascript -e 'display dialog "没有找到 ffmpeg。\n这个工具靠 ffmpeg 干活，必须先装。\n\n方法一（推荐）：\n打开「终端」，粘贴这一行回车：\nbrew install ffmpeg\n\n方法二：\n到 ffmpeg.org 下载 macOS 版，解压后把 ffmpeg 放进 /usr/local/bin\n\n装完再双击本文件。" buttons {"好"} default button 1 with icon caution'
  exit 1
fi

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
clear
exec "$PY" "程序/server.py"
