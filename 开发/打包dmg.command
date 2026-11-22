#!/bin/zsh
# 打包 dmg 安装版
#
# 和「构建app.command」的区别：
#   构建app.command → 生成本地用的轻外壳 .app（程序/ 还留在外面，方便改代码）
#   打包dmg.command → 生成自包含 .app（程序/ 打进 Contents/Resources/），
#                     再封成 dmg，拖进「应用程序」就能用
set -e
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"

VER=$(python3 - "$ROOT/程序/server.py" <<'PY'
import re, sys
s = open(sys.argv[1], encoding="utf-8").read()
print(re.search(r'^VERSION = "([^"]+)"', s, re.M).group(1))
PY
)

APP_NAME="LUT批量导出器"
STAGE="/tmp/lut_dmg_stage"
APP="$STAGE/$APP_NAME.app"
OUT="$ROOT/LUTBatchExporter_v${VER}_macOS.dmg"

echo "▶ 版本：v$VER"
echo "▶ 编译 Swift…"
swiftc -O -swift-version 5 -framework Cocoa -framework WebKit \
       native/main.swift -o /tmp/LUTBatch_build 2>&1 | sed 's/^/  /'

echo "▶ 组装自包含 .app…"
rm -rf "$STAGE"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp /tmp/LUTBatch_build "$APP/Contents/MacOS/LUTBatch"
chmod +x "$APP/Contents/MacOS/LUTBatch"
cp native/Info.plist "$APP/Contents/Info.plist"
[ -f native/AppIcon.icns ] && cp native/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"

# 代码进 Resources；配置文件是开发机私人数据，一律不带
rsync -a \
  --exclude 'config.json' --exclude 'lut_fav.json' \
  --exclude 'lut_hidden.json' --exclude 'lut_meta.json' \
  --exclude '__pycache__' --exclude '.DS_Store' \
  "$ROOT/程序" "$APP/Contents/Resources/"

echo "▶ 校验打包内容…"
setopt local_options null_glob
BAD=("$APP/Contents/Resources/程序"/*.json(N))
if [ ${#BAD[@]} -gt 0 ]; then
  echo "  ✗ Resources 里混进了私人配置，中止："
  printf '    %s\n' "${BAD[@]}"
  exit 1
fi
[ -f "$APP/Contents/Resources/程序/server.py" ] || { echo "  ✗ 缺 server.py"; exit 1; }
[ -d "$APP/Contents/Resources/程序/static" ]    || { echo "  ✗ 缺 static/";  exit 1; }
[ -d "$APP/Contents/Resources/程序/luts" ]      || { echo "  ✗ 缺 luts/";    exit 1; }
echo "  ✓ 代码齐、无私人数据"

echo "▶ 签名…"
codesign --force --deep --sign - --timestamp=none "$APP" >/dev/null 2>&1 \
  && echo "  ✓ ad-hoc 签名完成" \
  || echo "  （签名失败，本机仍可用；分发时可能被拦）"

echo "▶ 封 dmg…"
rm -rf /tmp/lut_dmg_root
mkdir -p /tmp/lut_dmg_root
cp -R "$APP" /tmp/lut_dmg_root/
ln -s /Applications /tmp/lut_dmg_root/Applications
cp "$ROOT/使用说明.html" /tmp/lut_dmg_root/使用说明.html 2>/dev/null || true

rm -f "$OUT"
hdiutil create -quiet -volname "$APP_NAME v$VER" \
  -srcfolder /tmp/lut_dmg_root -fs HFS+ \
  -format UDZO -imagekey zlib-level=9 "$OUT"

rm -rf "$STAGE" /tmp/lut_dmg_root /tmp/LUTBatch_build

SIZE=$(du -m "$OUT" | cut -f1)
echo ""
echo "✓ 完成：$OUT（${SIZE} MB）"
echo ""
echo "安装：双击 dmg → 把 $APP_NAME 拖进 Applications。"
echo "首次打开若被拦：在 app 上右键 →「打开」→ 再点「打开」。"
