#!/bin/zsh
# 一键自检：换电脑、升级 ffmpeg、或者怀疑工具坏了的时候双击这个
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
clear
/usr/bin/python3 "自检.py"
echo ""
echo "（看完可以关掉这个窗口）"
