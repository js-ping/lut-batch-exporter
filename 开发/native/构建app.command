#!/bin/zsh
# 重新编译原生外壳（生成本工具包根目录下的 LUT批量导出器.app）
# 只有改了 main.swift 才需要跑这个；正常使用直接双击 .app
set -e
cd "$(dirname "$0")"
ROOT="$(cd ../.. && pwd)"
APP="$ROOT/LUT批量导出器.app"

echo "▶ 编译 Swift…"
swiftc -O -swift-version 5 -framework Cocoa -framework WebKit main.swift -o /tmp/LUTBatch_build 2>&1 | sed 's/^/  /'

echo "▶ 组装 .app…"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp /tmp/LUTBatch_build "$APP/Contents/MacOS/LUTBatch"
cp Info.plist "$APP/Contents/Info.plist"
[ -f AppIcon.icns ] && cp AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
chmod +x "$APP/Contents/MacOS/LUTBatch"

# 本地临时签名，避免每次启动都被系统拦
codesign --force --sign - --timestamp=none "$APP" >/dev/null 2>&1 || echo "  （签名跳过，不影响本机运行）"

rm -f /tmp/LUTBatch_build
echo "✓ 完成：$APP"
echo ""
echo "双击 LUT批量导出器.app 即可使用，不需要浏览器。"
