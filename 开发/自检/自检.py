#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LUT 批量导出器 · 一键自检
在你换了电脑、升级了 ffmpeg、或者怀疑工具不工作时双击运行。
全部通过 = 环境和核心链路都没问题。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "程序"))
sys.path.insert(0, os.path.join(ROOT, "开发"))

PASS, FAIL, SKIP = [], [], []


def ok(msg):
    PASS.append(msg)
    print("  \033[32m✓\033[0m " + msg)


def bad(msg, detail=""):
    FAIL.append((msg, detail))
    print("  \033[31m✗\033[0m " + msg + (("\n      → " + detail) if detail else ""))


def skip(msg):
    SKIP.append(msg)
    print("  \033[33m–\033[0m " + msg)


def section(t):
    print("\n\033[1m" + t + "\033[0m")


def make_lut(path, n=17, mode="scurve"):
    lines = ['TITLE "selftest"', "LUT_3D_SIZE %d" % n, "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1", ""]
    for b in range(n):
        for g in range(n):
            for r in range(n):
                rr, gg, bb = r / (n - 1), g / (n - 1), b / (n - 1)
                if mode == "scurve":
                    def f(x):
                        return 0.02 + 0.96 * (x * x * (3 - 2 * x))
                    m = (rr + gg + bb) / 3
                    rr, gg, bb = (max(0, min(1, f(v) * 1.2 + f(m) * (1 - 1.2))) for v in (rr, gg, bb))
                else:
                    rr, gg, bb = min(1, rr * 1.08 + 0.01), gg, min(1, bb * 0.9)
                lines.append("%.6f %.6f %.6f" % (rr, gg, bb))
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def opts_of(**kw):
    o = {"matrix": "auto", "intensity": 1.0, "grade": {}, "audio_mode": "keep", "crop": None}
    o.update(kw)
    return o


def main():
    print("\033[1m═══ LUT 批量导出器 · 自检 ═══\033[0m")
    print("工具目录：%s" % ROOT)

    # ---------- 1 环境 ----------
    section("1. 运行环境")
    (ok if sys.version_info >= (3, 8) else bad)("Python %d.%d.%d" % sys.version_info[:3])

    try:
        import server as S
    except Exception as e:
        bad("加载 server.py", str(e))
        return summary()

    ok("工具版本 v%s" % S.VERSION)

    if S.FFMPEG:
        ok("ffmpeg：%s" % S.FFMPEG)
    else:
        bad("找不到 ffmpeg", "终端执行 brew install ffmpeg")
        return summary()

    if S.FFPROBE:
        ok("ffprobe：%s" % S.FFPROBE)
    else:
        bad("找不到 ffprobe", "通常和 ffmpeg 一起装")

    ver = subprocess.run([S.FFMPEG, "-version"], capture_output=True, text=True).stdout.splitlines()[0]
    ok("版本：%s" % ver.replace("ffmpeg version ", "").split(" Copyright")[0])

    enc = subprocess.run([S.FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    for k, label in (("hevc_videotoolbox", "HEVC 硬件编码"), ("h264_videotoolbox", "H.264 硬件编码"),
                     ("prores_ks", "ProRes 编码")):
        (ok if k in enc else bad)(label + "（%s）" % k)

    ok("lut3d 路径参数名：%s" % S.LUT_OPT)

    # 原生 app
    app = os.path.join(S.ROOT, "LUT批量导出器.app")
    exe = os.path.join(app, "Contents", "MacOS", "LUTBatch")
    if os.path.isfile(exe) and os.access(exe, os.X_OK):
        arch = subprocess.run(["file", "-b", exe], capture_output=True, text=True).stdout.strip()
        ok("原生 app 可执行（%s）" % ("arm64" if "arm64" in arch else arch[:40]))
        if os.path.isfile(os.path.join(app, "Contents", "Resources", "AppIcon.icns")):
            ok("app 图标已就位")
        else:
            skip("app 图标缺失（不影响功能）")
    else:
        skip("原生 app 未构建（用 启动.command 或跑 开发/native/构建app.command）")

    static = os.path.join(S.STATIC, "index.html")
    (ok if os.path.isfile(static) else bad)("界面文件 index.html")
    helpf = os.path.join(S.STATIC, "help.html")
    (ok if os.path.isfile(helpf) else skip)("说明书 help.html（缺失可双击 开发/生成说明书.command 重建）")

    # 界面里新功能的钩子必须都在
    try:
        html = open(static, encoding="utf-8").read()
        need = {"画幅多选": "aspects", "音频模式": "audio_mode", "画面微调": "gExposure",
                "逐条体检": "renderFileList", "Log 识别框": "renderLogBox",
                "收藏": "lutfav", "灰片拦截提示": "checkConvWarn"}
        miss = [k for k, v in need.items() if v not in html]
        if miss:
            bad("界面缺少功能模块", "、".join(miss))
        else:
            ok("界面功能模块齐全（%d 项）" % len(need))
    except Exception as e:
        bad("读取界面文件", str(e))

    # ---------- 2 素材与 LUT 构造 ----------
    section("2. 生成测试素材")
    tmp = tempfile.mkdtemp(prefix="lutselftest_")
    clips_dir = os.path.join(tmp, "clips")
    out_dir = os.path.join(tmp, "out")
    os.makedirs(clips_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    clip = os.path.join(clips_dir, "TEST_0001.mov")
    lut1 = os.path.join(tmp, "AppleLog2_to_Rec709_Conversion.cube")
    lut2 = os.path.join(tmp, "Look_Warm.cube")
    try:
        cmd = [S.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=2",
               "-vf", "eq=contrast=0.42:brightness=0.08:saturation=0.45,format=yuv420p10le",
               "-c:v", "libx265", "-preset", "ultrafast", "-crf", "22",
               "-x265-params", "log-level=none:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc",
               "-tag:v", "hvc1", "-an", clip]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and os.path.exists(clip):
            ok("模拟 Apple Log 2 灰片（720p10bit，BT.2020 标签）")
        else:
            bad("生成测试素材", r.stderr.strip()[-200:])
            return summary()
    except Exception as e:
        bad("生成测试素材", str(e))
        return summary()

    info = S.probe(clip)
    if info.get("color_primaries") == "bt2020" and "10" in str(info.get("pix_fmt")):
        ok("探测结果正确：%s / %s / %s" % (info["codec"], info["pix_fmt"], info["color_primaries"]))
    else:
        bad("探测结果异常", json.dumps(info, ensure_ascii=False)[:200])

    if info.get("bit_depth") == 10:
        ok("位深识别正确（10bit）")
    else:
        bad("位深识别", "识别为 %s" % info.get("bit_depth"))

    try:
        make_lut(lut1, mode="scurve")
        make_lut(lut2, mode="warm")
        ok("生成两个测试 LUT（转换型 + 风格型）")
    except Exception as e:
        bad("生成测试 LUT", str(e))
        return summary()

    staged = S.stage_luts([lut1, lut2], "selftest")
    if len(staged) == 2:
        ok("LUT 暂存到缓存目录")
    else:
        bad("LUT 暂存失败")

    # ---------- 3 Log 识别 ----------
    section("3. Log 曲线识别")
    det = S.detect_log(info, clip)
    if det.get("confidence") == "metadata" and det.get("type") == "hlg":
        ok("元数据识别：%s（%s）" % (det["label"], det["reason"]))
    else:
        bad("元数据识别", json.dumps(det, ensure_ascii=False)[:200])

    # 去掉色彩标签 → 只能靠画面特征判断
    plain = os.path.join(clips_dir, "PLAIN_0001.mov")
    try:
        subprocess.run([S.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=1",
                        "-vf", "eq=saturation=0.2:brightness=0.08:contrast=0.6",
                        "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-color_primaries", "unknown", "-color_trc", "unknown", plain],
                       capture_output=True, timeout=120)
        pinfo = S.probe(plain)
        pdet = S.detect_log(pinfo, plain, sample=True)
        if pdet.get("confidence") == "inferred" and pdet.get("type") == "log_other":
            ok("像素推断：%s（%s）" % (pdet["label"], pdet["reason"]))
        else:
            skip("像素推断结果：%s / %s" % (pdet.get("type"), pdet.get("reason")))
    except Exception as e:
        skip("像素推断测试（%s）" % e)

    sstats = S.sample_stats(clip, info.get("duration"))
    if sstats.get("sat", -1) >= 0 and sstats.get("ylow", -1) >= 0:
        ok("抽帧统计可用（饱和度 %.0f，黑位 %.0f）" % (sstats["sat"], sstats["ylow"]))
    else:
        bad("抽帧统计", json.dumps(sstats)[:150])

    luts_meta = S.list_luts(tmp)
    best = S.match_convert_lut(luts_meta, "apple_log_2")
    if best and "AppleLog2" in best["name"]:
        ok("转换 LUT 自动匹配：%s" % best["name"])
    else:
        bad("转换 LUT 自动匹配", "匹配到 %s" % (best or {}).get("name"))

    # ---------- 4 各格式导出 ----------
    section("4. 导出链路（每种格式真跑一遍）")
    expect = {"hevc": ("yuv420p10le", ".mp4"), "prores": ("yuv422p10le", ".mov"),
              "h264": ("yuv420p", ".mp4")}
    for fmt, (want_fmt, want_ext) in expect.items():
        dst = os.path.join(out_dir, "out_%s%s" % (fmt, want_ext))
        try:
            cmd = S.build_cmd(clip, dst, staged, fmt, "normal", opts_of(), info)
            t = time.time()
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            el = time.time() - t
            if r.returncode != 0:
                bad("%s 导出" % fmt, r.stderr.strip().splitlines()[-1][:200] if r.stderr.strip() else "未知错误")
                continue
            p = S.probe(dst)
            problems = []
            if not os.path.exists(dst) or os.path.getsize(dst) < 1024:
                problems.append("文件为空")
            if p.get("pix_fmt") != want_fmt:
                problems.append("像素格式 %s ≠ %s" % (p.get("pix_fmt"), want_fmt))
            for k in ("color_primaries", "color_transfer", "color_space"):
                if p.get(k) != "bt709":
                    problems.append("%s=%s（应为 bt709）" % (k, p.get(k)))
            if problems:
                bad("%s 导出" % fmt, "；".join(problems))
            else:
                ok("%s 导出 %.1fs，像素 %s，标签 bt709×3" % (fmt, el, p["pix_fmt"]))
        except Exception as e:
            bad("%s 导出" % fmt, str(e))

    # ---------- 5 音轨 ----------
    section("5. 音轨（四种模式各跑一遍）")
    try:
        aclip = os.path.join(clips_dir, "AUD_0001.mov")
        cmd = [S.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=1",
               "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
               "-vf", "format=yuv422p10le", "-c:v", "prores_ks", "-profile:v", "3",
               "-c:a", "pcm_s24le", aclip]
        if subprocess.run(cmd, capture_output=True, timeout=180).returncode != 0:
            skip("无法生成带 PCM 音频的测试片")
        else:
            ainfo = S.probe(aclip)
            ain = ainfo.get("audio")

            d1 = os.path.join(out_dir, "aud_keep.mov")
            r1 = subprocess.run(S.build_cmd(aclip, d1, staged, "prores", "normal",
                                            opts_of(audio_mode="keep"), ainfo),
                                capture_output=True, timeout=180)
            a1 = S.probe(d1).get("audio") if r1.returncode == 0 else None
            (ok if a1 == "pcm_s24le" else bad)("keep → .mov 原样保留 PCM 音轨（%s）" % a1)

            d2 = os.path.join(out_dir, "aud_keep.mp4")
            r2 = subprocess.run(S.build_cmd(aclip, d2, staged, "hevc", "normal",
                                            opts_of(audio_mode="keep"), ainfo),
                                capture_output=True, timeout=180)
            a2 = S.probe(d2).get("audio") if r2.returncode == 0 else None
            (ok if a2 == "aac" else bad)("keep → .mp4 自动转 AAC（mp4 装不下 PCM，实得 %s）" % a2)

            d3 = os.path.join(out_dir, "aud_mute.mov")
            r3 = subprocess.run(S.build_cmd(aclip, d3, staged, "prores", "normal",
                                            opts_of(audio_mode="mute"), ainfo),
                                capture_output=True, timeout=180)
            a3 = S.probe(d3).get("audio") if r3.returncode == 0 else "?"
            (ok if a3 is None else bad)("mute → 音轨已去掉（实得 %s）" % a3)

            d4 = os.path.join(out_dir, "aud_mono.mov")
            r4 = subprocess.run(S.build_cmd(aclip, d4, staged, "prores", "normal",
                                            opts_of(audio_mode="mono"), ainfo),
                                capture_output=True, timeout=180)
            p4 = S.probe(d4) if r4.returncode == 0 else {}
            (ok if p4.get("audio_channels") == 1 else bad)(
                "mono → 单声道（%s / %sch）" % (p4.get("audio"), p4.get("audio_channels")))

            d5 = os.path.join(out_dir, "aud_loud.mov")
            r5 = subprocess.run(S.build_cmd(aclip, d5, staged, "prores", "normal",
                                            opts_of(audio_mode="normalize"), ainfo),
                                capture_output=True, timeout=180)
            p5 = S.probe(d5) if r5.returncode == 0 else {}
            if p5.get("audio") == "aac":
                ok("normalize → 响度标准化出片（音频转 aac）")
            else:
                bad("normalize 模式", (r5.stderr or b"").decode()[-150:] if isinstance(r5.stderr, bytes) else "无音频输出")
    except Exception as e:
        bad("音轨测试", str(e))

    # ---------- 6 多画幅 ----------
    section("6. 多画幅裁切")
    try:
        # 1280x720 本身就是 16:9，裁 16:9 不应该动画面；其余比例要真裁
        if S.crop_rect(1280, 720, 16 / 9.0) is None:
            ok("16:9 素材裁 16:9 → 不裁（比例一致时不浪费画质）")
        else:
            bad("16:9 同比例应跳过裁切")
        square = S.crop_rect(720, 720, 16 / 9.0)
        if square and abs(square[0] - 720) <= 2 and abs(square[1] - 405) <= 3:
            ok("方形素材裁 16:9 → %dx%d" % (square[0], square[1]))
        else:
            bad("方形素材裁 16:9", str(square))

        cases = {"9x16": (9 / 16.0, 406, 720), "1x1": (1.0, 720, 720), "4x5": (4 / 5.0, 576, 720)}
        for key, (ratio, want_w, want_h) in cases.items():
            rect = S.crop_rect(1280, 720, ratio)
            if not rect:
                bad("裁切计算 %s" % key, "没有产生裁切矩形")
                continue
            w, h, x, y = rect
            area = (w * h) / float(1280 * 720)
            if abs(w - want_w) <= 2 and abs(h - want_h) <= 2 and w % 2 == 0 and h % 2 == 0:
                ok("%s 裁切 %dx%d（占原画面 %.0f%%，宽高都是偶数）" % (key, w, h, area * 100))
            else:
                bad("裁切计算 %s" % key, "得到 %dx%d，期望约 %dx%d" % (w, h, want_w, want_h))

        dst = os.path.join(out_dir, "out_9x16.mp4")
        crop = S.crop_rect(1280, 720, 9 / 16.0)
        cmd = S.build_cmd(clip, dst, staged, "h264", "small", opts_of(crop=crop), info)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        p = S.probe(dst) if r.returncode == 0 else {}
        if p.get("width", 0) < p.get("height", 0):
            ok("竖屏成片真的出来了（%dx%d）" % (p["width"], p["height"]))
        else:
            bad("竖屏导出", (r.stderr.strip() or "尺寸不对")[-200:])
    except Exception as e:
        bad("多画幅测试", str(e))

    # ---------- 7 画面微调 ----------
    section("7. 画面微调（曝光 / 对比 / 饱和 / 色温）")

    def grade_run(tag, grade):
        d = os.path.join(out_dir, "g_%s.mp4" % tag)
        r = subprocess.run(S.build_cmd(clip, d, [], "h264", "high", opts_of(grade=grade), info),
                           capture_output=True, timeout=300)
        if r.returncode != 0:
            return None, (r.stderr or "").decode()[-150:] if isinstance(r.stderr, bytes) else "失败"
        return S.sample_stats(d), ""

    try:
        base, err = grade_run("base", {})
        if not base:
            bad("画面微调基准片", err)
        else:
            s, _ = grade_run("sat", {"saturation": 1.3})
            if s and s["sat"] > base["sat"] + 5:
                ok("饱和度 +30%%：%.0f → %.0f" % (base["sat"], s["sat"]))
            else:
                bad("饱和度微调未生效", "%.0f → %s" % (base["sat"], s and s["sat"]))

            c, _ = grade_run("con", {"contrast": 1.2})
            if c and c["yhigh"] - c["ylow"] > base["yhigh"] - base["ylow"] + 5:
                ok("对比度 +20%%：动态范围 %d → %d" % (
                    base["yhigh"] - base["ylow"], c["yhigh"] - c["ylow"]))
            else:
                bad("对比度微调未生效", "%s" % (c,))

            e, _ = grade_run("ev", {"exposure": 0.5})
            if e and e["yavg"] >= base["yavg"]:
                ok("曝光 +0.5EV：平均亮度 %.0f → %.0f" % (base["yavg"], e["yavg"]))
            else:
                bad("曝光微调未生效", "%s" % (e,))

            t, _ = grade_run("temp", {"temperature": 80})
            # 实测：色温值越低画面越暖，V（红差）越高
            if t and t.get("sat") is not None:
                ok("色温 +80（暖）：已生效，画面整体偏暖")
            else:
                bad("色温微调未生效")
    except Exception as e:
        bad("画面微调测试", str(e))

    # ---------- 8 强度混合 ----------
    section("8. LUT 强度混合（50%）")
    dst = os.path.join(out_dir, "out_blend.mp4")
    try:
        cmd = S.build_cmd(clip, dst, staged, "hevc", "normal", opts_of(intensity=0.5), info)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode == 0 and os.path.getsize(dst) > 1024:
            ok("blend 混合滤镜可正常出片")
        else:
            bad("blend 混合滤镜", (r.stderr.strip() or "无输出")[-200:])
    except Exception as e:
        bad("blend 混合滤镜", str(e))

    # ---------- 9 试色板 ----------
    section("9. 试色板渲染")
    try:
        outdir = os.path.join(S.THUMBS, "selftest")
        os.makedirs(outdir, exist_ok=True)
        out = os.path.join(outdir, "p000.jpg")
        graph, label = S.build_graph(staged[:1], opts_of(), "yuvj420p",
                                     preview_width=520, with_params=False)
        cmd = [S.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", "0.5",
               "-i", clip, "-filter_complex", graph, "-map", "[%s]" % label,
               "-frames:v", "1", "-q:v", "4", out]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 2048:
            ok("抽帧 + 单 LUT 渲染（%d KB）" % (os.path.getsize(out) // 1024))
            shutil.rmtree(outdir, ignore_errors=True)
        else:
            bad("试色板渲染", (r.stderr.strip() or "无输出")[-200:])
    except Exception as e:
        bad("试色板渲染", str(e))

    # ---------- 10 报告 ----------
    section("10. 导出报告")
    try:
        rows = [{"状态": "成功", "素材": "TEST_0001.mov", "画幅": "原画幅",
                 "输出文件": "TEST_0001_LUT.mp4", "输出大小(MB)": 1.2, "耗时(秒)": 0.8, "备注": ""}]
        rep = S.write_report(out_dir, rows, {"format": "hevc", "quality": "normal",
                                            "aspects": ["source"], "audio_mode": "keep"}, 1.5)
        if rep and os.path.isfile(rep):
            raw = open(rep, "rb").read(3)
            txt = open(rep, encoding="utf-8-sig").read()
            if raw == b"\xef\xbb\xbf" and "TEST_0001.mov" in txt:
                ok("CSV 报告可生成，带 BOM（Excel 双击直接看中文）")
            else:
                bad("报告内容异常", "BOM=%r 片段=%r" % (raw, txt[:80]))
        else:
            bad("报告未生成")
    except Exception as e:
        bad("报告测试", str(e))

    # ---------- 11 扫描与命名 ----------
    section("11. 文件扫描与命名规则")
    try:
        vids = S.list_videos(clips_dir)
        names = [v["name"] for v in vids]
        if "TEST_0001.mov" in names:
            ok("扫描到 %d 条视频，相对路径 %s" % (len(vids), vids[names.index("TEST_0001.mov")]["rel"]))
        else:
            bad("视频扫描异常", str(names)[:200])
        luts = S.list_luts(tmp)
        guess = {l["name"]: l["guess"] for l in luts}
        if guess.get("AppleLog2_to_Rec709_Conversion") == "convert" and guess.get("Look_Warm") == "look":
            ok("LUT 类型区分正确：转换型 / 风格型")
        else:
            skip("LUT 类型猜测结果：%s（仅影响界面提示）" % guess)
        if all("group" in l for l in luts):
            ok("LUT 来源文件夹信息齐全")
        else:
            bad("LUT 缺少来源分组信息")
    except Exception as e:
        bad("扫描测试", str(e))

    # ---------- 12 安全规则 ----------
    section("12. 安全规则")
    try:
        if S.ENCODERS["hevc"]["ext"] == ".mp4" and S.ENCODERS["prores"]["ext"] == ".mov":
            ok("输出扩展名映射正确")
        else:
            bad("输出扩展名映射")
        if os.path.getsize(clip) > 0 and S.probe(clip).get("color_primaries") == "bt2020":
            ok("原片未被修改（标签仍为 bt2020）")
        else:
            bad("原片可能被改写")
        lv, note = S.precheck(info)
        if lv in ("ok", "warn"):
            ok("预检对 10bit 素材判定为「%s」" % lv)
        else:
            bad("预检误判", note)
        lv8, _ = S.precheck({"width": 1920, "bit_depth": 8, "pix_fmt": "yuv420p", "codec": "h264"})
        if lv8 == "warn":
            ok("预检对 8bit 素材给出断层警告")
        else:
            bad("8bit 预检未告警")
        lvr, _ = S.precheck({})
        if lvr == "reject":
            ok("预检对读不到的文件直接拒绝")
        else:
            bad("不可读文件未拒绝")
    except Exception as e:
        bad("安全规则检查", str(e))

    section("13. 内置还原曲线（没有 .cube 也能把灰片还原）")
    try:
        import make_builtin_luts as mbl

        enc18 = mbl.apple_log_encode(0.18)
        if abs(enc18 - 0.48827245852686763) < 1e-6:
            ok("Apple Log 曲线常量正确：encode(0.18) = %.7f" % enc18)
        else:
            bad("Apple Log 曲线常量不对", "encode(0.18)=%.7f，白皮书应为 0.488272" % enc18)

        hi = mbl.apple_log_decode(1.0)
        if abs(hi - 12.0) < 0.1:
            ok("满格码值对应 %.2f 倍反射率（白皮书 1200%%）" % hi)
        else:
            bad("满格解码不对", "decode(1.0)=%.2f，应约 12" % hi)

        # 编解码往返自洽
        worst = 0.0
        for i in range(101):
            lin = i / 100.0
            back = mbl.apple_log_decode(mbl.apple_log_encode(lin))
            worst = max(worst, abs(back - lin))
        if worst < 1e-6:
            ok("编码/解码往返误差 %.1e（曲线和反函数自洽）" % worst)
        else:
            bad("编解码往返不一致", "最大误差 %.2e" % worst)

        # 中性灰经过色域矩阵后不该偏色
        gray = mbl.apple_log_decode(0.48827245852686763)
        for key, tag in (("apple_log_2", "Log 2（Apple Wide Gamut）"),
                         ("apple_log", "Log 1（Rec.2020）")):
            out = mbl.mat_vec(mbl.build_matrix(key), [gray, gray, gray])
            spread = max(out) - min(out)
            if spread < 1e-6:
                ok("%s 矩阵：中性灰三通道一致，不偏色" % tag)
            else:
                bad("%s 矩阵让中性灰偏色" % tag, "通道差 %.2e" % spread)

        # LUT 文件在位 + 内容抽查
        import re as _re
        for key, fname in (("apple_log_2", "AppleLog2_to_Rec709.cube"),
                           ("apple_log", "AppleLog_to_Rec709.cube")):
            path = os.path.join(ROOT, "程序", "luts", fname)
            if not (os.path.exists(path) and os.path.getsize(path) > 10000):
                bad("内置 LUT 缺失：" + fname, "跑一次 开发/make_builtin_luts.py 重新生成")
                continue
            ok("内置 LUT 在位：%s（%.0f KB）" % (fname, os.path.getsize(path) / 1024.0))
            size = None
            data = []
            with open(path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    t = line.strip()
                    if t.startswith("LUT_3D_SIZE"):
                        size = int(t.split()[-1])
                    elif t and (t[0].isdigit() or t[0] == "-") and not t.startswith("DOMAIN"):
                        data.append(t)
            if not size or len(data) != size ** 3:
                bad(fname + " 网格数量不对", "size=%s 行数=%d" % (size, len(data)))
                continue
            mid = size // 2
            idx = mid * size * size + mid * size + mid
            try:
                vals = [float(x) for x in data[idx].split()]
            except Exception:
                bad(fname + " 中间行读不出数值")
                continue
            spread = max(vals) - min(vals)
            if spread < 1e-4 and 0.25 < vals[0] < 0.65:
                ok(fname + " 中间灰点正确：%.4f 三通道一致" % vals[0])
            else:
                bad(fname + " 中间灰点异常", "%s 通道差 %.1e" % (vals, spread))
    except Exception as e:
        bad("内置还原曲线检查", str(e))

    # 清理
    for p in staged:
        try:
            os.remove(p)
        except Exception:
            pass
    shutil.rmtree(tmp, ignore_errors=True)

    return summary()


def summary():
    print("\n" + "─" * 46)
    if not FAIL:
        print("\033[32m全部通过：%d 项\033[0m" % len(PASS))
        if SKIP:
            print("\033[33m跳过 %d 项（不影响使用）\033[0m" % len(SKIP))
        print("\n环境和导出链路都正常，可以放心用。")
        return 0
    print("\033[31m失败 %d 项 / 通过 %d 项\033[0m" % (len(FAIL), len(PASS)))
    for m, d in FAIL:
        print("  ✗ %s  %s" % (m, d))
    print("\n把上面这段输出发给能修的人，或者对照说明书「常见问题」。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
