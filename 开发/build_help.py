#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 开发/使用说明.md 编译成两个 HTML：
  程序/static/help.html   （工具内「说明书」页）
  使用说明.html            （双击直接看的独立页面）
零第三方依赖，只维护 md 一份。
"""

import os
import re
import html

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "开发", "使用说明.md")
OUT_INLINE = os.path.join(ROOT, "程序", "static", "help.html")
OUT_STANDALONE = os.path.join(ROOT, "使用说明.html")


def inline(text):
    """行内标记：先转义，再处理 code / 粗体 / 斜体 / 链接。"""
    if not text:
        return ""
    codes = []

    def keep(m):
        codes.append(html.escape(m.group(1)))
        return "\x00%d\x00" % (len(codes) - 1)

    text = re.sub(r"`([^`]+)`", keep, text)
    text = html.escape(text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\*\w])\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    def back(m):
        return "<code>%s</code>" % codes[int(m.group(1))]

    return re.sub(r"\x00(\d+)\x00", back, text)


def convert(md):
    lines = md.replace("\r\n", "\n").split("\n")
    out = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        s = line.strip()

        # 空行
        if not s:
            i += 1
            continue

        # 代码块
        if s.startswith("```"):
            lang = s[3:].strip()
            buf = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            out.append('<pre class="code"><code>%s</code></pre>' % html.escape("\n".join(buf)))
            continue

        # 分隔线
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
            out.append("<hr>")
            i += 1
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            lv = len(m.group(1))
            txt = inline(m.group(2).strip())
            anchor = re.sub(r"[^\w\u4e00-\u9fa5]+", "-", m.group(2).strip()).strip("-").lower()
            out.append('<h%d id="%s">%s</h%d>' % (lv, anchor, txt, lv))
            i += 1
            continue

        # 表格
        if s.startswith("|") and i + 1 < n and re.match(r"^\|[\s:\-\|]+\|?$", lines[i + 1].strip()):
            header = [c.strip() for c in s.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            t = ['<div class="tw"><table><thead><tr>']
            for h in header:
                t.append("<th>%s</th>" % inline(h))
            t.append("</tr></thead><tbody>")
            for r in rows:
                t.append("<tr>")
                for k, c in enumerate(r):
                    lab = header[k] if k < len(header) else ""
                    t.append('<td data-label="%s">%s</td>' % (html.escape(lab), inline(c)))
                t.append("</tr>")
            t.append("</tbody></table></div>")
            out.append("".join(t))
            continue

        # 引用
        if s.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            out.append("<blockquote>%s</blockquote>" % inline(" ".join(buf)))
            continue

        # 有序列表
        if re.match(r"^\d+\.\s", s):
            items = []
            while i < n and re.match(r"^\d+\.\s", lines[i].strip()):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i].strip()))
                i += 1
            out.append("<ol>%s</ol>" % "".join("<li>%s</li>" % inline(x) for x in items))
            continue

        # 无序列表
        if re.match(r"^[-*+]\s", s):
            items = []
            while i < n and re.match(r"^[-*+]\s", lines[i].strip()):
                items.append(re.sub(r"^[-*+]\s+", "", lines[i].strip()))
                i += 1
            out.append("<ul>%s</ul>" % "".join("<li>%s</li>" % inline(x) for x in items))
            continue

        # 段落
        buf = []
        while i < n and lines[i].strip() and not re.match(
                r"^(#{1,6}\s|```|\||>|\d+\.\s|[-*+]\s|-{3,}$)", lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        out.append("<p>%s</p>" % inline(" ".join(buf)))

    return "\n".join(out)


TPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root{{
  --bg:#0b0d10;--panel:#14171c;--line:#242a33;--txt:#e8ecf2;--dim:#8d97a6;--dim2:#5f6875;
  --acc:#e0a35c;--code:#0d1015;--mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;
}}
@media (prefers-color-scheme: light){{
  :root{{--bg:#fbfaf8;--panel:#fff;--line:#e6e2db;--txt:#1b1e23;--dim:#5d6673;--dim2:#8b929c;
        --acc:#a86a26;--code:#f4f1ec}}
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--txt);line-height:1.75;
  font:15px/1.75 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",sans-serif;
  -webkit-font-smoothing:antialiased;padding:0 0 80px}}
.top{{position:sticky;top:0;backdrop-filter:blur(14px);
  background:color-mix(in srgb, var(--bg) 88%, transparent);
  border-bottom:1px solid var(--line);padding:12px 24px;display:flex;align-items:center;gap:12px;z-index:9}}
.top b{{font-size:14px}}
.top a{{margin-left:auto;font-size:12.5px;color:var(--acc);text-decoration:none;
  border:1px solid var(--line);padding:5px 11px;border-radius:20px}}
.top a:hover{{border-color:var(--acc)}}
main{{max-width:820px;margin:0 auto;padding:34px 24px 0}}
h1{{font-size:27px;line-height:1.3;letter-spacing:-.4px;margin:8px 0 18px;font-weight:700}}
h2{{font-size:19px;margin:38px 0 14px;padding-bottom:9px;border-bottom:1px solid var(--line);
  letter-spacing:-.2px;font-weight:650}}
h3{{font-size:15.5px;margin:26px 0 10px;color:var(--acc);font-weight:650}}
h4{{font-size:14px;margin:20px 0 8px;color:var(--dim)}}
p{{margin:11px 0;color:var(--txt)}}
a{{color:var(--acc);text-decoration:none;border-bottom:1px solid color-mix(in srgb,var(--acc) 35%,transparent)}}
a:hover{{border-bottom-color:var(--acc)}}
ul,ol{{margin:11px 0 11px 24px}}
li{{margin:6px 0}}
li::marker{{color:var(--dim2)}}
code{{font-family:var(--mono);font-size:.88em;background:var(--code);
  border:1px solid var(--line);padding:1.5px 5px;border-radius:5px;color:var(--acc)}}
pre.code{{background:var(--code);border:1px solid var(--line);border-radius:10px;
  padding:14px 16px;overflow:auto;margin:14px 0}}
pre.code code{{background:none;border:none;padding:0;color:var(--txt);font-size:12.5px;line-height:1.6}}
blockquote{{border-left:3px solid var(--acc);background:color-mix(in srgb,var(--acc) 8%,transparent);
  padding:11px 16px;margin:16px 0;border-radius:0 8px 8px 0;color:var(--dim)}}
blockquote strong{{color:var(--txt)}}
hr{{border:none;border-top:1px solid var(--line);margin:30px 0}}
strong{{font-weight:650;color:var(--txt)}}
.tw{{overflow-x:auto;margin:16px 0}}
table{{width:100%;border-collapse:collapse;font-size:13.5px}}
th{{background:color-mix(in srgb,var(--acc) 9%,transparent);color:var(--txt);
  text-align:left;padding:9px 12px;border:1px solid var(--line);font-weight:650;white-space:nowrap}}
td{{padding:9px 12px;border:1px solid var(--line);vertical-align:top}}
tbody tr:nth-child(even){{background:color-mix(in srgb,var(--line) 22%,transparent)}}
@media (max-width:640px){{
  body{{font-size:14.5px}}
  main{{padding:22px 16px 0}}
  h1{{font-size:22px}}
  .tw{{overflow:visible}}
  table,tbody,tr,td{{display:block;width:100%}}
  thead{{display:none}}
  tr{{border:1px solid var(--line);border-radius:10px;margin-bottom:10px;
    padding:6px 2px;background:var(--panel)}}
  td{{border:none;border-bottom:1px dashed var(--line);padding:8px 12px;display:flex;gap:10px}}
  tr td:last-child{{border-bottom:none}}
  td::before{{content:attr(data-label);flex:0 0 88px;color:var(--dim2);font-size:12px;font-weight:600}}
}}
@media print{{
  .top{{display:none}} body{{padding:0;font-size:11pt}}
  a{{color:inherit;border:none}} h2{{page-break-after:avoid}}
  pre,table,blockquote{{page-break-inside:avoid}}
}}
</style>
</head>
<body>
<div class="top"><b>📖 使用说明</b><a href="/">← 回到工具</a></div>
<main>
{body}
</main>
</body>
</html>
"""


def build():
    with open(SRC, "r", encoding="utf-8") as f:
        md = f.read()
    title = "使用说明"
    for line in md.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    page = TPL.format(title=html.escape(title), body=convert(md))
    os.makedirs(os.path.dirname(OUT_INLINE), exist_ok=True)
    with open(OUT_INLINE, "w", encoding="utf-8") as f:
        f.write(page)
    with open(OUT_STANDALONE, "w", encoding="utf-8") as f:
        f.write(page)
    print("✓ 已生成：")
    print("  " + OUT_INLINE)
    print("  " + OUT_STANDALONE)


if __name__ == "__main__":
    build()
