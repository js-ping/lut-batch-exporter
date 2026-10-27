#!/bin/zsh
# 一键发版：提交改动 → 打 tag → 推 GitHub → 建 Release → 传安装包
# 用法：改完东西后，双击这个文件
#
# 注意：本机 /etc/hosts 里 WorkBuddy 写了「GitHub 加速」条目，
# 把 api.github.com 指到了 web 服务器 IP，直接调 API 会被 301 劫持。
# 下面用 --resolve 强制连真实 API IP 绕开。

set -e
cd "$(dirname "$0")"

REPO_OWNER="js-ping"
REPO_NAME="lut-batch-exporter"
REPO_SSH="git@github.com:${REPO_OWNER}/${REPO_NAME}.git"
API_IP="140.82.112.6"          # api.github.com 真实 IP，hosts 劫持时用它
API="https://api.github.com"

# ---------- 1. 版本号 ----------
VER=$(grep -m1 'VERSION = ' 程序/server.py | sed 's/.*"\(.*\)".*/\1/')
if [ -z "$VER" ]; then
  echo "✗ 读不到 程序/server.py 里的 VERSION"
  read -p "按回车关闭…"; exit 1
fi
echo "当前版本：$VER"

# ---------- 2. 提交 ----------
if [ -n "$(git status --porcelain)" ]; then
  echo "▶ 发现未提交的改动，先提交…"
  git add -A
  git commit -m "v${VER} 更新"
fi

# ---------- 3. tag + push ----------
if git rev-parse "v${VER}" >/dev/null 2>&1; then
  echo "▶ tag v${VER} 已存在，跳过"
else
  git tag -a "v${VER}" -m "v${VER}"
fi
echo "▶ 推送代码与 tag…"
git push -u origin main 2>&1 | sed 's/^/  /'
git push -f origin "v${VER}" 2>&1 | sed 's/^/  /'

# ---------- 4. 取凭据（在钥匙串里，不落到磁盘）----------
eval $(printf "protocol=https\nhost=github.com\n\n" | git credential fill 2>/dev/null \
      | grep -E '^(username|password)=' | sed 's/^/export GH_/')
if [ -z "$GH_password" ]; then
  echo "✗ 钥匙串里没有 GitHub 凭据，先 git push 一次让它记住"
  read -p "按回车关闭…"; exit 1
fi

# ---------- 5. 打安装包 ----------
echo "▶ 打包 macOS 安装包…"
rm -rf /tmp/lut_pack && mkdir -p /tmp/lut_pack/LUT批量导出器
rsync -a \
  --exclude '.git' --exclude '开发' --exclude '运行记录' \
  --exclude '.DS_Store' --exclude '发布新版本.command' \
  ./ /tmp/lut_pack/LUT批量导出器/
python3 - <<'PY'
import os, zipfile
SRC='/tmp/lut_pack/LUT批量导出器'
OUT='/tmp/LUTBatchExporter_vX_macOS.zip'
if os.path.exists(OUT): os.remove(OUT)
with zipfile.ZipFile(OUT,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    for root,dirs,files in os.walk(SRC):
        dirs.sort(); files.sort()
        for f in files:
            full=os.path.join(root,f)
            z.write(full, os.path.relpath(full, os.path.dirname(SRC)))
print('  包大小：', os.path.getsize(OUT), 'bytes')
PY
# 用版本号命名
ZIP="/tmp/LUTBatchExporter_v${VER}_macOS.zip"
mv /tmp/LUTBatchExporter_vX_macOS.zip "$ZIP"

# ---------- 6. 建 Release（已存在就复用）----------
echo "▶ 建 GitHub Release…"
REL_JSON=$(curl -s --noproxy '*' --resolve "api.github.com:443:${API_IP}" \
  -H "Authorization: Bearer ${GH_password}" \
  -H "Accept: application/vnd.github+json" \
  "${API}/repos/${REPO_OWNER}/${REPO_NAME}/releases/tags/v${VER}")

REL_ID=$(echo "$REL_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id') or '')" 2>/dev/null || true)

if [ -z "$REL_ID" ]; then
  python3 - "$VER" <<'PY' > /tmp/rel_payload.json
import sys, re, json
ver = sys.argv[1]
try:
    s = open('开发/CHANGELOG.md', encoding='utf-8').read()
    m = re.search(r'## v' + re.escape(ver) + r'\b.*?(?=\n---\n)', s, re.S)
    body = m.group(0).strip() if m else f'v{ver}'
except Exception:
    body = f'v{ver}'
body += f"\n\n---\n\n**安装**：下载 `LUTBatchExporter_v{ver}_macOS.zip`，解压后双击 `LUT批量导出器.app`。\n"
json.dump({"tag_name": f"v{ver}", "name": f"v{ver}", "body": body,
           "draft": False, "prerelease": False},
          open('/dev/stdout','w'), ensure_ascii=False)
PY
  REL_ID=$(curl -s --noproxy '*' --resolve "api.github.com:443:${API_IP}" \
    -X POST -H "Authorization: Bearer ${GH_password}" \
    -H "Accept: application/vnd.github+json" -H "Content-Type: application/json" \
    --data @/tmp/rel_payload.json \
    "${API}/repos/${REPO_OWNER}/${REPO_NAME}/releases" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id') or '')")
  echo "  新建 Release id=$REL_ID"
else
  echo "  复用已有 Release id=$REL_ID"
fi

if [ -z "$REL_ID" ]; then
  echo "✗ Release 创建失败"
  read -p "按回车关闭…"; exit 1
fi

# ---------- 7. 传附件 ----------
# GitHub 上传接口会丢非 ASCII 文件名，所以附件用 ASCII 名
ZIP_NAME="LUTBatchExporter_v${VER}_macOS.zip"
echo "▶ 上传 ${ZIP_NAME}…"

# 先删同名旧附件
ASSET_ID=$(curl -s --noproxy '*' --resolve "api.github.com:443:${API_IP}" \
  -H "Authorization: Bearer ${GH_password}" -H "Accept: application/vnd.github+json" \
  "${API}/repos/${REPO_OWNER}/${REPO_NAME}/releases/${REL_ID}/assets" \
  | python3 -c "
import sys,json
for a in json.load(sys.stdin):
    if a['name']=='${ZIP_NAME}': print(a['id']); break
" 2>/dev/null || true)
[ -n "$ASSET_ID" ] && curl -s --noproxy '*' --resolve "api.github.com:443:${API_IP}" \
  -X DELETE -H "Authorization: Bearer ${GH_password}" \
  "${API}/repos/${REPO_OWNER}/${REPO_NAME}/releases/assets/${ASSET_ID}" -o /dev/null

python3 - "$ZIP" "$ZIP_NAME" "$REL_ID" "$GH_password" <<'PY'
import sys, os, json, http.client, urllib.parse
zip_path, name, rel_id, token = sys.argv[1:5]
data = open(zip_path,'rb').read()
q = urllib.parse.urlencode({"name": name})
conn = http.client.HTTPSConnection("uploads.github.com", 443, timeout=300)
conn.request("POST", f"/repos/js-ping/lut-batch-exporter/releases/{rel_id}/assets?{q}",
             body=data,
             headers={"Authorization":"Bearer "+token, "Content-Type":"application/zip",
                      "Accept":"application/vnd.github+json",
                      "Content-Length":str(len(data)), "User-Agent":"lut-release"})
r = conn.getresponse(); body = r.read().decode()
d = json.loads(body)
if r.status in (200,201):
    print(f"  ✓ {d['name']}  {d['size']:,} bytes")
    print(f"    {d['browser_download_url']}")
else:
    print(f"  ✗ 上传失败 HTTP {r.status}: {d.get('message')}")
    sys.exit(1)
PY

echo ""
echo "════════════════════════════════════"
echo "  ✓ 发版完成：v${VER}"
echo "    https://github.com/${REPO_OWNER}/${REPO_NAME}/releases/tag/v${VER}"
echo "════════════════════════════════════"
echo ""
read -p "按回车关闭…"
