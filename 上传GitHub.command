#!/bin/zsh
# 双击把本工具推上 GitHub
# 用之前先在网页上建好空仓库：https://github.com/new
#   名字填 lut-batch-exporter，什么都不用勾，然后回来双击这个文件
cd "$(dirname "$0")"

REPO="git@github.com:js-ping/lut-batch-exporter.git"

if [ ! -d .git ]; then
  git init -q
  git add -A
  git commit -q -m "v1.5.0 勾选交互重做 + 目录精简" || true
  git branch -M main
fi

git remote remove origin 2>/dev/null
git remote add origin "$REPO"

echo "▶ 正在推送到 $REPO ..."
if git push -u origin main 2>&1; then
  echo ""
  echo "✓ 推送成功！仓库地址：https://github.com/js-ping/lut-batch-exporter"
else
  echo ""
  echo "✗ 推不上去。两种可能："
  echo "  1. 你还没在网页上建仓库 —— 打开 https://github.com/new"
  echo "     名字填 lut-batch-exporter，三个勾选框都不要勾，建完再双击本文件"
  echo "  2. 仓库名不一样 —— 用文本编辑器打开本文件，改第一行 REPO= 那个地址"
fi
echo ""
read -p "按回车关闭…"
