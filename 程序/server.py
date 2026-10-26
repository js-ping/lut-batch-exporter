#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LUT 批量导出器 —— 本地服务端
Apple Log 2 灰片批量套 LUT 导出，底层调用 ffmpeg。

只依赖 Python 3 标准库 + ffmpeg。
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

APP_NAME = "LUT 批量导出器"
VERSION = "1.5.0"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 工具包根目录
PROG = os.path.join(ROOT, "程序")
STATIC = os.path.join(PROG, "static")
RECORDS = os.path.join(ROOT, "运行记录")
CACHE = os.path.join(RECORDS, ".cache")
THUMBS = os.path.join(RECORDS, "试色板")
CONFIG_PATH = os.path.join(PROG, "config.json")
FAV_PATH = os.path.join(PROG, "lut_fav.json")
HIDDEN_PATH = os.path.join(PROG, "lut_hidden.json")
PROBE_CACHE_PATH = os.path.join(CACHE, "probe_cache.json")
DETECT_CACHE_PATH = os.path.join(CACHE, "detect_cache.json")

# 内置转换 LUT：由 make_builtin_luts.py 按 Apple 公开曲线常量算出来，
# 让用户不用自己去找 .cube 也能把灰片还原成正常画面。
BUILTIN_LUT_DIR = os.path.join(PROG, "luts")
BUILTIN_LUTS = {
    "apple_log_2": {
        "file": "AppleLog2_to_Rec709.cube",
        "label": "Apple Log 2 → Rec.709",
        "desc": "Apple Wide Gamut，iPhone 17 Pro / 17 Pro Max 拍的",
        "short": "内置 · Log 2 还原",
    },
    "apple_log": {
        "file": "AppleLog_to_Rec709.cube",
        "label": "Apple Log → Rec.709",
        "desc": "Rec.2020，iPhone 15 Pro / 16 Pro 拍的",
        "short": "内置 · Log 1 还原",
    },
}

VIDEO_EXT = (".mov", ".mp4", ".m4v", ".MOV", ".MP4", ".M4V")
LUT_EXT = (".cube", ".CUBE")

os.makedirs(RECORDS, exist_ok=True)
os.makedirs(CACHE, exist_ok=True)
os.makedirs(THUMBS, exist_ok=True)


# ---------------------------------------------------------------- 工具函数

def find_bin(name):
    """在常见路径中查找可执行文件。"""
    for p in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        f = os.path.join(p, name)
        if os.path.isfile(f) and os.access(f, os.X_OK):
            return f
    return shutil.which(name)


FFMPEG = find_bin("ffmpeg")
FFPROBE = find_bin("ffprobe")


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%.1f PB" % n


def fmt_dur(sec):
    sec = int(max(0, sec))
    return "%02d:%02d" % (sec // 60, sec % 60)


def probe(path):
    """读取视频流信息（含元数据标签）。"""
    if not FFPROBE:
        return {}
    cmd = [FFPROBE, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", path]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
        data = json.loads(out or "{}")
    except Exception:
        return {}
    v = None
    a = None
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and v is None:
            v = s
        elif s.get("codec_type") == "audio" and a is None:
            a = s
    if not v:
        return {}
    fmt = data.get("format", {})

    tags = {}
    for k, val in (fmt.get("tags") or {}).items():
        tags[str(k)] = str(val)
    for k, val in (v.get("tags") or {}).items():
        tags.setdefault(str(k), str(val))

    fps = 0.0
    try:
        num, den = (v.get("avg_frame_rate") or "0/1").split("/")
        if float(den) > 0:
            fps = float(num) / float(den)
    except Exception:
        pass
    dur = 0.0
    try:
        dur = float(fmt.get("duration") or v.get("duration") or 0)
    except Exception:
        pass
    return {
        "codec": v.get("codec_name", "?"),
        "profile": v.get("profile", ""),
        "pix_fmt": v.get("pix_fmt", "?"),
        "bit_depth": guess_bit_depth(v.get("pix_fmt", "")),
        "width": v.get("width", 0),
        "height": v.get("height", 0),
        "fps": round(fps, 2),
        "duration": dur,
        "bitrate": int(fmt.get("bit_rate") or 0),
        "color_primaries": v.get("color_primaries", "unknown"),
        "color_transfer": v.get("color_transfer", "unknown"),
        "color_space": v.get("color_space", "unknown"),
        "color_range": v.get("color_range", "unknown"),
        "audio": (a.get("codec_name") if a else None),
        "audio_channels": (a.get("channels") if a else 0),
        "size": int(fmt.get("size") or 0),
        "tags": tags,
    }


def guess_bit_depth(pix_fmt):
    """从像素格式推断位深。"""
    p = str(pix_fmt or "").lower()
    for n in ("16", "12", "10", "9"):
        if n in p:
            return int(n)
    if p.startswith(("gbrp", "yuv4", "yuvj4", "gray")) or "p" in p:
        return 8
    return 8


# ---- probe 结果缓存：同一个文件第二次扫描直接读缓存 ----

_PROBE_CACHE = None
_PROBE_LOCK = threading.Lock()


def _cache_load():
    global _PROBE_CACHE
    if _PROBE_CACHE is None:
        try:
            with open(PROBE_CACHE_PATH, "r", encoding="utf-8") as f:
                _PROBE_CACHE = json.load(f)
        except Exception:
            _PROBE_CACHE = {}
    return _PROBE_CACHE


def _cache_save():
    global _PROBE_CACHE
    c = _PROBE_CACHE or {}
    if len(c) > 4000:                       # 防止无限膨胀
        keys = list(c.keys())[-2000:]
        _PROBE_CACHE = {k: c[k] for k in keys}
    try:
        with open(PROBE_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_PROBE_CACHE, f)
    except Exception:
        pass


def probe_cached(path):
    try:
        st = os.stat(path)
    except OSError:
        return {}
    key = "%s|%d|%d" % (path, int(st.st_mtime), st.st_size)
    c = _cache_load()
    if key in c:
        return c[key]
    info = probe(path)
    if info:
        with _PROBE_LOCK:
            c[key] = info
    return info


# ---------------------------------------------------------------- Log 识别
#
# 两级判据：
#   1) 元数据：ProRes/HEVC 里 Apple 会写色彩传输特性与私有标签 —— 最可靠
#      （color_transfer=bt709 说明已经转换过；arib-std-b67=HLG；smpte2084=PQ）
#   2) 像素特征：抽一帧算饱和度和黑位。Log 素材天生低饱和、黑位抬高。
#      这一步只能判定"像不像 Log"，分不出 Apple Log 还是 Apple Log 2，
#      所以置信度标 inferred，界面提示用户确认机型。

LOG_TYPES = {
    "apple_log_2": {"label": "Apple Log 2", "short": "Log 2"},
    "apple_log": {"label": "Apple Log", "short": "Log"},
    "hlg": {"label": "HLG", "short": "HLG"},
    "pq": {"label": "PQ / HDR10", "short": "PQ"},
    "rec709": {"label": "Rec.709（已转换）", "short": "709"},
    "log_other": {"label": "其他 Log 曲线", "short": "Log"},
    "mixed": {"label": "素材类型不一致", "short": "混合"},
    "unknown": {"label": "无法判定", "short": "?"},
}

_LOG2_TOKENS = ("applevisionlog2", "apple log 2", "apple log2", "applelog2",
                "applelog_2", "log2", "log_2", "log-2", "vlog2")
_LOG1_TOKENS = ("applevisionlog", "apple log", "applelog", "apple log 1")


def detect_log(info, path=None, sample=False):
    """判定素材属于哪种色彩曲线。返回 dict(type, label, confidence, reason)。"""
    info = info or {}
    tags = info.get("tags") or {}
    blob = " ".join("%s %s" % (k, v) for k, v in tags.items()).lower()
    ct = str(info.get("color_transfer") or "unknown").lower()

    # ---- 1) 元数据
    if any(t in blob for t in _LOG2_TOKENS):
        return {"type": "apple_log_2", "label": "Apple Log 2",
                "confidence": "key", "reason": "文件元数据标注 Apple Log 2"}
    if any(t in blob for t in _LOG1_TOKENS):
        return {"type": "apple_log", "label": "Apple Log",
                "confidence": "key", "reason": "文件元数据标注 Apple Log"}
    if ct == "arib-std-b67":
        return {"type": "hlg", "label": "HLG", "confidence": "metadata",
                "reason": "传输特性 arib-std-b67（HLG）"}
    if ct == "smpte2084":
        return {"type": "pq", "label": "PQ / HDR10", "confidence": "metadata",
                "reason": "传输特性 smpte2084（PQ）"}
    if ct == "bt709":
        return {"type": "rec709", "label": "Rec.709（已转换）", "confidence": "metadata",
                "reason": "传输特性已是 BT.709，说明转换过了"}

    # ---- 2) 像素推断
    # 判据核心是饱和度：套过 Log 曲线的画面饱和度几乎不可能上到 38/255，
    # 正常片也很难低到 30 以下。黑位（YLOW）只作为辅助说明，不参与阈值。
    if sample and path:
        st = sample_stats(path, info.get("duration"))
        if st:
            sat, ylow = st.get("sat", 0), st.get("ylow", 0)
            detail = "饱和度 %.0f/255，亮度 10%% 分位 %.0f/255" % (sat, ylow)
            if sat < 30:
                return {"type": "log_other", "label": "疑似 Log（低饱和）",
                        "confidence": "inferred",
                        "reason": detail + ("，黑位明显抬高" if ylow > 45 else ""),
                        "stats": st}
            if sat < 38:
                return {"type": "log_other", "label": "偏向 Log（饱和度偏低）",
                        "confidence": "inferred",
                        "reason": detail + "，建议用试色板确认一下", "stats": st}
            return {"type": "rec709", "label": "Rec.709（正常片）", "confidence": "inferred",
                    "reason": detail, "stats": st}

    return {"type": "unknown", "label": "无法判定", "confidence": "none",
            "reason": "没有色彩元数据，做像素检测也没有结论"}


def sample_stats(path, duration=None):
    """抽一帧统计饱和度与亮度分布（固定归一化到 8bit 尺度，判据才稳定）。"""
    if not FFMPEG:
        return {}
    at = 1.0
    try:
        if duration:
            at = min(max(0.2, duration * 0.3), max(0.2, duration - 0.2))
    except Exception:
        pass
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
           "-ss", "%.2f" % at, "-i", path, "-frames:v", "1",
           "-vf", "scale=320:-2,format=yuv420p,signalstats,metadata=print:file=-",
           "-f", "null", "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=90).stdout
    except Exception:
        return {}
    vals = {}
    for k in ("YLOW", "YAVG", "YHIGH", "SATAVG", "SATMAX", "YMIN", "YMAX"):
        m = re.search(r"signalstats\.%s=([\d.]+)" % k, out)
        if m:
            try:
                vals[k] = float(m.group(1))
            except Exception:
                pass
    if not vals:
        return {}
    return {
        "sat": vals.get("SATAVG", 0),
        "satmax": vals.get("SATMAX", 0),
        "ylow": vals.get("YLOW", 0),
        "yavg": vals.get("YAVG", 0),
        "yhigh": vals.get("YHIGH", 0),
    }


_DETECT_CACHE = None
_DETECT_LOCK = threading.Lock()


def detect_log_cached(path, info, allow_sample=True):
    """带缓存的 Log 判定。像素推断要抽帧解码，同一个文件只做一次。"""
    global _DETECT_CACHE
    base = detect_log(info, None)
    if base.get("confidence") != "none":
        return base
    if not allow_sample:
        return base

    try:
        st = os.stat(path)
        key = "%s|%d|%d" % (path, int(st.st_mtime), st.st_size)
    except OSError:
        return base

    with _DETECT_LOCK:
        if _DETECT_CACHE is None:
            _DETECT_CACHE = load_json(DETECT_CACHE_PATH, {})
        if key in _DETECT_CACHE:
            return _DETECT_CACHE[key]

    res = detect_log(info, path, sample=True)
    with _DETECT_LOCK:
        _DETECT_CACHE[key] = res
        if len(_DETECT_CACHE) > 3000:
            keys = list(_DETECT_CACHE.keys())[-1500:]
            _DETECT_CACHE = {k: _DETECT_CACHE[k] for k in keys}
        save_json(DETECT_CACHE_PATH, _DETECT_CACHE)
    return res


def match_convert_lut(luts, log_type):
    """按识别结果在 LUT 库里挑最合适的转换 LUT。"""
    if log_type not in ("apple_log_2", "apple_log", "log_other", "hlg", "pq"):
        return None
    best, best_score = None, 0
    for l in luts or []:
        n = (l.get("name") or "").lower().replace(" ", "")
        score = 0
        if log_type == "apple_log_2":
            if "applelog2" in n or "log2" in n or "log_2" in n or "log-2" in n:
                score += 12
            elif "applelog" in n:
                score += 4          # 用旧曲线不理想，但比没有强
        elif log_type == "apple_log":
            if "applelog" in n or "log" in n:
                score += 8
            if "log2" in n:
                score -= 6          # 明显错的曲线要排除
        elif log_type in ("hlg", "pq"):
            if any(t in n for t in ("hlg", "hdr", "pq", "2084", "2020")):
                score += 10
        else:
            if "log" in n:
                score += 6
        if any(t in n for t in ("torec709", "to709", "rec709", "rec.709", "conversion",
                               "convert", "transform", "cst", "torec")):
            score += 5
        if any(t in n for t in ("technical", "input", "inputlut")):
            score += 2
        if l.get("guess") == "convert":
            score += 2
        if score > best_score:
            best, best_score = l, score
    return best


# ---------------------------------------------------------------- 文件扫描

def list_videos(folder):
    items = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn.startswith(".") or not fn.lower().endswith((".mov", ".mp4", ".m4v")):
                continue
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, folder)
            items.append({"name": fn, "rel": rel, "path": full, "size": st.st_size})
    items.sort(key=lambda x: natural_key(x["rel"]))
    return items


def list_luts(folder):
    items = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn.startswith(".") or not fn.lower().endswith(".cube"):
                continue
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, folder)
            items.append({
                "name": os.path.splitext(fn)[0],
                "file": fn,
                "rel": rel,
                "group": os.path.dirname(rel) or ".",
                "path": full,
                "size": st.st_size,
                "guess": guess_lut_type(fn),
            })
    items.sort(key=lambda x: natural_key(x["rel"]))
    return items


def builtin_lut_list():
    """内置转换 LUT 列表（不需要用户准备 .cube 文件）。"""
    out = []
    for key, v in BUILTIN_LUTS.items():
        p = os.path.join(BUILTIN_LUT_DIR, v["file"])
        if os.path.exists(p):
            out.append({"key": key, "label": v["label"], "desc": v["desc"],
                        "short": v["short"], "path": p,
                        "size": os.path.getsize(p)})
    return out


def builtin_lut_for(log_type):
    """按 Log 类型挑内置转换 LUT。分不清 1/2 时按 Log 2（新机型）算。"""
    key = "apple_log" if log_type == "apple_log" else "apple_log_2"
    p = os.path.join(BUILTIN_LUT_DIR, BUILTIN_LUTS[key]["file"])
    return p if os.path.exists(p) else ""


def is_builtin_lut(path):
    try:
        return os.path.dirname(os.path.abspath(path)) == os.path.abspath(BUILTIN_LUT_DIR)
    except Exception:
        return False


def guess_lut_type(filename):
    """猜 LUT 用途：转换型（Log→709）还是风格型。"""
    n = filename.lower()
    convert_tokens = ("applelog", "apple_log", "apple log", "log2", "log-2", "log_2",
                      "logtorec", "log_to_rec", "log-rec", "log→", "conversion",
                      "convert", "cst", "technical", "transform", "input",
                      "vlog", "slog", "clog", "flog", "hlg", "2084")
    if any(t in n for t in convert_tokens):
        return "convert"
    return "look"


def _as_apple_string(s):
    """安全地生成 AppleScript 字符串字面量。"""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def pick_folder(prompt):
    """调起 macOS 原生选择文件夹对话框。"""
    script = "POSIX path of (choose folder with prompt %s)" % _as_apple_string(prompt)
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            p = r.stdout.strip()
            return p.rstrip("/") if p else None
        err = (r.stderr or "").strip()
        if err and "-128" not in err:      # -128 = 用户取消
            print("[选择文件夹] osascript 报错：%s" % err, flush=True)
        return None
    except Exception as e:
        print("[选择文件夹] 异常：%s" % e, flush=True)
        return None


def notify(title, message):
    """发一条 macOS 系统通知（长时间批处理结束时用）。"""
    if os.environ.get("LUT_NO_NOTIFY"):
        return
    script = 'display notification %s with title %s' % (
        _as_apple_string(message), _as_apple_string(title))
    try:
        subprocess.Popen(["osascript", "-e", script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_config():
    return load_json(CONFIG_PATH, {})


def save_config(cfg):
    save_json(CONFIG_PATH, cfg)


def load_fav():
    d = load_json(FAV_PATH, {})
    if not isinstance(d, dict):
        d = {}
    d.setdefault("favorites", [])
    d.setdefault("recent", [])
    return d


def remember_lut(paths):
    """记住最近用过的 LUT，最多 12 个。"""
    d = load_fav()
    rec = [p for p in d.get("recent", []) if p not in paths]
    d["recent"] = (list(paths) + rec)[:12]
    save_json(FAV_PATH, d)


def load_hidden():
    """已从列表移除的 LUT。只影响列表显示，磁盘上的 .cube 文件一个都不动。"""
    d = load_json(HIDDEN_PATH, {})
    if not isinstance(d, dict):
        return []
    v = d.get("hidden")
    return [str(x) for x in v] if isinstance(v, list) else []


def save_hidden(items):
    save_json(HIDDEN_PATH, {"hidden": sorted(set(items))})


# ---------------------------------------------------------------- 滤镜与命令构建

ENCODERS = {
    "prores_hq": {
        "label": "ProRes 422 HQ（归档母版）",
        "ext": ".mov",
        "pix_fmt": "yuv422p10le",
        "vcodec": ["-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0"],
        "note": "画质无损级，文件很大，适合留底和后续再剪",
    },
    "prores": {
        "label": "ProRes 422（轻量归档）",
        "ext": ".mov",
        "pix_fmt": "yuv422p10le",
        "vcodec": ["-c:v", "prores_ks", "-profile:v", "2", "-vendor", "apl0"],
        "note": "比 HQ 小约 30%，肉眼几乎无差",
    },
    "hevc": {
        "label": "HEVC 10bit（发平台）",
        "ext": ".mp4",
        "pix_fmt": "p010le",
        "vcodec": ["-c:v", "hevc_videotoolbox", "-profile:v", "main10", "-tag:v", "hvc1"],
        "note": "硬件编码，体积小，手机/剪映/微信都吃得下",
    },
    "h264": {
        "label": "H.264 8bit（最兼容）",
        "ext": ".mp4",
        "pix_fmt": "yuv420p",
        "vcodec": ["-c:v", "h264_videotoolbox", "-profile:v", "high", "-tag:v", "avc1"],
        "note": "老设备/老平台兜底用，会掉到 8bit",
    },
}

QUALITY = {
    "high": {"label": "高", "bitrate": 60},
    "normal": {"label": "标准", "bitrate": 40},
    "small": {"label": "小", "bitrate": 20},
}

# 一次导出多个画幅：素材只解码一遍，同时产出横屏/竖屏/方形版本
ASPECTS = {
    "source": {"label": "原画幅", "ratio": None, "suffix": "", "note": "保持素材原始比例"},
    "16x9": {"label": "16:9 横屏", "ratio": 16 / 9.0, "suffix": "_16x9", "note": "B站 / YouTube"},
    "9x16": {"label": "9:16 竖屏", "ratio": 9 / 16.0, "suffix": "_9x16", "note": "Reels / 抖音 / 视频号"},
    "1x1": {"label": "1:1 方形", "ratio": 1.0, "suffix": "_1x1", "note": "朋友圈 / 头像位"},
    "4x5": {"label": "4:5 竖幅", "ratio": 4 / 5.0, "suffix": "_4x5", "note": "小红书 / Instagram"},
}

AUDIO_MODES = {
    "keep": {"label": "保持原样", "note": "能直拷就直拷，装不下才转 AAC"},
    "mute": {"label": "静音", "note": "去掉音轨"},
    "mono": {"label": "转单声道", "note": "给口播/采访统一声道"},
    "normalize": {"label": "响度标准化", "note": "-16 LUFS，各条素材音量一致"},
}


def detect_lut_opt():
    """不同 ffmpeg 版本 lut3d 的路径参数名不一样（file / filename）。"""
    try:
        out = subprocess.run([FFMPEG, "-hide_banner", "-h", "filter=lut3d"],
                             capture_output=True, text=True, timeout=20).stdout
        if re.search(r"^\s*filename\s+<string>", out, re.M):
            return "filename"
        if re.search(r"^\s*file\s+<string>", out, re.M):
            return "file"
    except Exception:
        pass
    return "file"


LUT_OPT = detect_lut_opt()


def crop_rect(w, h, ratio):
    """按目标比例算中心裁切矩形（Python 端算好，避免滤镜表达式转义问题）。"""
    if not ratio or not w or not h:
        return None
    sw = float(w) / float(h)
    if abs(sw - ratio) < 0.008:
        return None                     # 比例基本一致，不裁
    if sw > ratio:
        nw, nh = int(round(h * ratio)), h
    else:
        nw, nh = w, int(round(w / ratio))
    nw -= nw % 2
    nh -= nh % 2
    nw = max(2, min(nw, w - (w % 2)))
    nh = max(2, min(nh, h - (h % 2)))
    return (nw, nh, (w - nw) // 2, (h - nh) // 2)


def build_graph(luts, opts, final_fmt, preview_width=None, with_params=True, audio_filter=None):
    """构建 filter_complex 图，返回 (graph, out_label)。

    opts 支持：matrix / intensity / crop / grade(exposure,contrast,saturation,temperature)
    """
    matrix = opts.get("matrix", "auto")
    intensity = opts.get("intensity", 1.0)
    crop = opts.get("crop")
    grade = opts.get("grade") or {}

    pre = []
    if matrix and matrix != "auto":
        pre.append("scale=in_color_matrix=%s:in_range=tv" % matrix)
    pre.append("format=gbrp10le")
    head = ",".join(pre)

    def lut(name):
        return "lut3d=%s=%s:interp=tetrahedral" % (LUT_OPT, name)

    tail_bits = []
    if crop:
        tail_bits.append("crop=%d:%d:%d:%d" % crop)
    if preview_width:
        tail_bits.append("scale=%d:-2" % preview_width)

    # 画面微调：曝光用 EV（乘性，接近真实曝光），对比/饱和/色温各一个
    ev = float(grade.get("exposure") or 0)
    ct = float(grade.get("contrast") or 1.0)
    sa = float(grade.get("saturation") or 1.0)
    tp = float(grade.get("temperature") or 0)
    if abs(ev) > 0.01:
        tail_bits.append("exposure=exposure=%.2f:black=0" % max(-3.0, min(3.0, ev)))
    if abs(ct - 1.0) > 0.005 or abs(sa - 1.0) > 0.005:
        tail_bits.append("eq=contrast=%.3f:saturation=%.3f" % (
            max(0.2, min(2.0, ct)), max(0.0, min(2.5, sa))))
    if abs(tp) > 1:
        # 实测：色温值越低画面越暖。+100 暖 → 4000K，-100 冷 → 9000K
        kelvin = int(max(3000, min(10000, 6500 - tp * 25)))
        tail_bits.append("colortemperature=temperature=%d:mix=1" % kelvin)

    tail_bits.append("format=%s" % final_fmt)
    if with_params:
        tail_bits.append("setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709")
    tail = ",".join(tail_bits)

    if not luts:
        graph = "[0:v]%s,%s[outv]" % (head, tail)
    elif intensity is None or intensity >= 0.999:
        apply_all = ",".join(lut(n) for n in luts)
        graph = "[0:v]%s,%s,%s[outv]" % (head, apply_all, tail)
    else:
        # 强度 < 100%：最后一个 LUT 用混合比例
        if len(luts) > 1:
            before = ",".join(lut(n) for n in luts[:-1])
            graph = "[0:v]%s,%s,split=2[bA][bB];" % (head, before)
        else:
            graph = "[0:v]%s,split=2[bA][bB];" % head
        graph += "[bB]%s[bG];" % lut(luts[-1])
        graph += "[bA][bG]blend=all_mode=normal:all_opacity=%.3f[bM];" % intensity
        graph += "[bM]%s[outv]" % tail

    if audio_filter:
        graph += ";[0:a]%s[aout]" % audio_filter
    return graph, "outv"


def stage_luts(lut_paths, tag="job"):
    """把 LUT 复制到缓存目录，用安全文件名，彻底规避 ffmpeg 路径转义问题。"""
    staged = []
    for i, p in enumerate(lut_paths):
        if not p:
            continue
        dst = os.path.join(CACHE, "%s_lut%d.cube" % (tag, i + 1))
        shutil.copyfile(p, dst)
        staged.append(dst)
    return staged


def audio_plan(mode, ext, src_codec, has_audio):
    """返回 (map_args, codec_args, audio_filter)。"""
    if not has_audio or mode == "mute":
        return (["-an"], [], None)
    if mode == "mono":
        return (["-map", "0:a?"], ["-c:a", "aac", "-b:a", "128k", "-ac", "1"], None)
    if mode == "normalize":
        return (["-map", "[aout]"], ["-c:a", "aac", "-b:a", "192k"],
                "loudnorm=I=-16:TP=-1.5:LRA=11")
    # keep
    if ext == ".mp4" and str(src_codec or "").startswith("pcm"):
        return (["-map", "0:a?"], ["-c:a", "aac", "-b:a", "256k"], None)
    return (["-map", "0:a?"], ["-c:a", "copy"], None)


def build_cmd(src, dst, staged_luts, fmt_key, quality, opts, info, overwrite=True):
    enc = ENCODERS[fmt_key]
    mode = opts.get("audio_mode", "keep")
    has_audio = bool(info.get("audio"))
    map_args, codec_args, afilter = audio_plan(mode, enc["ext"], info.get("audio"), has_audio)

    graph, label = build_graph(staged_luts, opts, enc["pix_fmt"], audio_filter=afilter)
    br = QUALITY.get(quality, QUALITY["normal"])["bitrate"]

    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if overwrite:
        cmd.append("-y")
    cmd += ["-i", src, "-filter_complex", graph, "-map", "[%s]" % label]
    cmd += map_args

    cmd += enc["vcodec"]
    if fmt_key in ("hevc", "h264"):
        cmd += ["-b:v", "%dM" % br, "-maxrate", "%dM" % int(br * 1.4),
                "-bufsize", "%dM" % (br * 2), "-allow_sw", "1"]
    cmd += ["-pix_fmt", enc["pix_fmt"]]
    cmd += codec_args

    cmd += ["-map_metadata", "0", "-map_chapters", "-1"]
    if enc["ext"] == ".mp4":
        cmd += ["-movflags", "+faststart"]
    cmd += ["-max_muxing_queue_size", "2048", "-progress", "pipe:1", "-nostats", dst]
    return cmd


# ---------------------------------------------------------------- 任务状态

JOB = {
    "running": False, "stopping": False, "total": 0, "done": 0,
    "failed": 0, "skipped": 0, "stopped": 0, "current": "", "percent": 0.0,
    "file_percent": 0.0, "speed": "", "eta": "", "log": [], "errors": [],
    "started_at": 0, "finished_at": 0, "out_dir": "", "total_bytes": 0,
    "done_bytes": 0, "report": "", "aspects": [],
}
JOB_LOCK = threading.Lock()
RUNNING_PROC = {"p": None}          # 当前正在跑的 ffmpeg，退出时一并结束


def jlog(msg, level="info"):
    line = {"t": time.strftime("%H:%M:%S"), "m": str(msg), "lv": level}
    with JOB_LOCK:
        JOB["log"].append(line)
        if len(JOB["log"]) > 600:
            del JOB["log"][:200]
    try:
        print("[%s] %s" % (line["t"], msg), flush=True)
    except Exception:
        pass


# ---------------------------------------------------------------- 导出报告

REPORT_COLS = ["状态", "素材", "画幅", "源分辨率", "帧率", "时长(秒)", "源编码", "位深",
               "输出文件", "输出分辨率", "输出大小(MB)", "耗时(秒)", "备注"]


def write_report(out_dir, rows, cfg, elapsed):
    """写一份 CSV 报告（带 BOM，Excel 直接双击能看中文）。"""
    if not rows:
        return ""
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, "_导出报告_%s.csv" % ts)

    def cell(v):
        s = "" if v is None else str(v)
        return '"' + s.replace('"', '""') + '"'

    try:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("# %s v%s 导出报告  生成时间 %s  耗时 %s\n" % (
                APP_NAME, VERSION, time.strftime("%Y-%m-%d %H:%M:%S"), fmt_dur(elapsed)))
            f.write("# 输出格式 %s / 质量 %s / 画幅 %s / 音频 %s\n" % (
                ENCODERS.get(cfg.get("format"), {}).get("label", "?"),
                QUALITY.get(cfg.get("quality"), {}).get("label", "?"),
                "、".join(ASPECTS[a]["label"] for a in cfg.get("aspects", ["source"])
                         if a in ASPECTS),
                AUDIO_MODES.get(cfg.get("audio_mode", "keep"), {}).get("label", "?")))
            f.write(",".join(cell(c) for c in REPORT_COLS) + "\n")
            for r in rows:
                f.write(",".join(cell(r.get(c, "")) for c in REPORT_COLS) + "\n")
        return path
    except Exception as e:
        print("[报告] 写入失败：%s" % e, flush=True)
        return ""


# ---------------------------------------------------------------- 主循环

def precheck(info):
    """输出前预检：能处理 / 需要注意 / 直接拒绝。"""
    if not info:
        return ("reject", "无法读取文件信息")
    notes, level = [], "ok"
    if info.get("bit_depth") and info["bit_depth"] <= 8:
        notes.append("源素材是 8bit，套 Log 转换 LUT 容易出现断层")
        level = "warn"
    if not info.get("width"):
        return ("reject", "读不到画面尺寸")
    if info.get("codec") in ("hevc", "h264") and str(info.get("pix_fmt", "")).startswith("yuvj"):
        notes.append("全范围（full range）素材，色阶可能偏移")
        level = "warn"
    return (level, "；".join(notes))


def run_job(cfg):
    global JOB
    src_dir = cfg["input_dir"]
    out_dir = cfg["output_dir"]
    fmt_key = cfg.get("format", "hevc")
    enc = ENCODERS[fmt_key]
    luts = [x for x in cfg.get("luts", []) if x]
    suffix = cfg.get("suffix", "")
    skip_existing = cfg.get("skip_existing", True)
    aspects = [a for a in (cfg.get("aspects") or ["source"]) if a in ASPECTS] or ["source"]

    opts = {
        "matrix": cfg.get("matrix", "auto"),
        "intensity": cfg.get("intensity", 1.0),
        "grade": cfg.get("grade") or {},
        "audio_mode": cfg.get("audio_mode", "keep"),
    }

    files = list_videos(src_dir)
    tasks = [(it, a) for it in files for a in aspects]
    with JOB_LOCK:
        JOB["total"] = len(tasks)
        JOB["aspects"] = aspects

    jlog("发现 %d 条素材 × %d 个画幅 = %d 个输出任务" % (len(files), len(aspects), len(tasks)))
    try:
        if (os.path.abspath(out_dir).rstrip("/") == os.path.abspath(src_dir).rstrip("/")):
            jlog("⚠ 导出目录和素材目录是同一个，输出会和原片混在一起，"
                 "下次扫描还会把导出结果当成素材", "warn")
    except Exception:
        pass
    jlog("LUT 链：%s" % ("  →  ".join(os.path.basename(x) for x in luts) or "（未选 LUT，仅做标签修正）"))
    jlog("输出格式：%s ／ 画幅：%s ／ 音频：%s" % (
        enc["label"], "、".join(ASPECTS[a]["label"] for a in aspects),
        AUDIO_MODES.get(opts["audio_mode"], {}).get("label", "?")))

    staged = stage_luts(luts, "job")
    if len(staged) != len(luts):
        jlog("LUT 暂存失败，任务中止", "error")
        with JOB_LOCK:
            JOB["running"] = False
        return
    remember_lut(luts)

    t0 = time.time()
    n = 0
    rows = []
    info_cache = {}

    for item, aspect in tasks:
        with JOB_LOCK:
            if JOB["stopping"]:
                jlog("已手动停止", "warn")
                break
            JOB["current"] = item["rel"] + (("  ·  " + ASPECTS[aspect]["label"]) if len(aspects) > 1 else "")
            JOB["file_percent"] = 0.0
        n += 1

        rel = item["rel"]
        base = os.path.splitext(os.path.basename(rel))[0]
        sub = os.path.dirname(rel)
        dst_name = base + (suffix or "") + ASPECTS[aspect]["suffix"] + enc["ext"]
        dst_dir = os.path.join(out_dir, sub) if sub else out_dir
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, dst_name)

        if skip_existing and os.path.exists(dst) and os.path.getsize(dst) > 1024:
            jlog("[%d/%d] 跳过（已存在）：%s" % (n, len(tasks), dst_name), "skip")
            with JOB_LOCK:
                JOB["skipped"] += 1
                JOB["done"] += 1
            rows.append({"状态": "跳过", "素材": rel, "画幅": ASPECTS[aspect]["label"],
                         "输出文件": dst_name, "备注": "目标已存在"})
            continue

        if rel not in info_cache:
            info_cache[rel] = probe(item["path"])
        info = info_cache[rel]

        level, note = precheck(info)
        if level == "reject":
            with JOB_LOCK:
                JOB["failed"] += 1
                JOB["done"] += 1
                JOB["errors"].append({"file": rel, "err": note})
            jlog("  ✗ 跳过 %s：%s" % (rel, note), "error")
            rows.append({"状态": "失败", "素材": rel, "画幅": ASPECTS[aspect]["label"],
                         "输出文件": dst_name, "备注": note})
            continue
        if level == "warn":
            jlog("  ⚠ %s：%s" % (os.path.basename(rel), note), "warn")

        dur = info.get("duration") or 0
        o = dict(opts)
        o["crop"] = crop_rect(info.get("width"), info.get("height"),
                              ASPECTS[aspect]["ratio"])

        jlog("[%d/%d] %s  %s %s %dx%d %.0ffps %.1fs" % (
            n, len(tasks), dst_name, info.get("codec", "?"), info.get("pix_fmt", "?"),
            info.get("width", 0), info.get("height", 0), info.get("fps", 0), dur))

        cmd = build_cmd(item["path"], dst, staged, fmt_key, cfg.get("quality", "normal"),
                        o, info)
        tf = time.time()
        ok, err, stopped = exec_ffmpeg(cmd, dur)
        took = time.time() - tf

        if stopped and not ok:
            # 用户按了停止：删掉半成品。不删的话下次续跑会把它当成已完成而跳过
            try:
                if os.path.exists(dst):
                    os.remove(dst)
            except Exception:
                pass
            with JOB_LOCK:
                JOB["stopped"] += 1
                JOB["stopping"] = True
                JOB["current"] = ""
            jlog("  ⏹ 已停止，半成品已删除：%s" % dst_name, "warn")
            rows.append({"状态": "已停止", "素材": rel, "画幅": ASPECTS[aspect]["label"],
                         "输出文件": dst_name, "备注": "手动停止，半成品已删除"})
            break

        if ok and os.path.exists(dst):
            try:
                sz = os.path.getsize(dst)
            except Exception:
                sz = 0
            with JOB_LOCK:
                JOB["done"] += 1
                JOB["done_bytes"] += sz
            out_info = probe(dst)
            jlog("  ✓ %s（%s，耗时 %s）" % (dst_name, human_size(sz), fmt_dur(took)), "ok")
            rows.append({
                "状态": "成功", "素材": rel, "画幅": ASPECTS[aspect]["label"],
                "源分辨率": "%dx%d" % (info.get("width", 0), info.get("height", 0)),
                "帧率": info.get("fps", ""), "时长(秒)": round(dur, 2),
                "源编码": info.get("codec", ""), "位深": info.get("bit_depth", ""),
                "输出文件": dst_name,
                "输出分辨率": "%dx%d" % (out_info.get("width", 0), out_info.get("height", 0)),
                "输出大小(MB)": round(sz / 1048576.0, 1),
                "耗时(秒)": round(took, 1), "备注": note,
            })
        else:
            with JOB_LOCK:
                JOB["failed"] += 1
                JOB["done"] += 1
                JOB["errors"].append({"file": rel, "err": err})
            jlog("  ✗ 失败 %s：%s" % (rel, err.splitlines()[-1] if err else "未知错误"), "error")
            rows.append({"状态": "失败", "素材": rel, "画幅": ASPECTS[aspect]["label"],
                         "输出文件": dst_name, "耗时(秒)": round(took, 1),
                         "备注": (err.splitlines()[-1] if err else "未知错误")})

    el = time.time() - t0
    with JOB_LOCK:
        JOB["running"] = False
        JOB["stopping"] = False
        JOB["current"] = ""
        done = JOB["done"]
        failed = JOB["failed"]
        skipped = JOB["skipped"]
        stopped_n = JOB["stopped"]
        if not stopped_n:
            JOB["percent"] = 100.0
        JOB["finished_at"] = time.time()

    report = write_report(out_dir, rows, cfg, el)
    if report:
        with JOB_LOCK:
            JOB["report"] = report
        jlog("导出报告：%s" % os.path.basename(report), "ok")

    if stopped_n:
        jlog("已停止：成功 %d，失败 %d，跳过 %d，中断 %d，用时 %s" % (
            max(0, done - failed - skipped), failed, skipped, stopped_n, fmt_dur(el)), "warn")
    else:
        jlog("全部结束：成功 %d，失败 %d，跳过 %d，用时 %s" % (
            max(0, done - failed - skipped), failed, skipped, fmt_dur(el)), "ok")

    if el > 45:
        notify("LUT 批量导出完成",
               "成功 %d 条，失败 %d 条，跳过 %d 条，用时 %s" % (
                   max(0, done - failed - skipped), failed, skipped, fmt_dur(el)))

    for p in staged:
        try:
            os.remove(p)
        except Exception:
            pass


def stop_running_proc(grace=1.2):
    """立刻掐掉当前 ffmpeg。先 SIGTERM 让它自己收尾，超时直接 SIGKILL。"""
    proc = RUNNING_PROC.get("p")
    if not proc or proc.poll() is not None:
        return False
    try:
        proc.terminate()
    except Exception:
        return False
    t0 = time.time()
    while time.time() - t0 < grace:
        if proc.poll() is not None:
            return True
        time.sleep(0.05)
    try:
        proc.kill()
    except Exception:
        pass
    return True


def exec_ffmpeg(cmd, duration):
    """执行 ffmpeg 并解析进度。返回 (成功, 错误输出, 是否被用户中断)。"""
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        RUNNING_PROC["p"] = p
    except Exception as e:
        return False, str(e), False

    err_buf = []
    t_start = time.time()
    interrupted = {"v": False}

    def read_err():
        for line in p.stderr:
            line = line.rstrip()
            if line:
                err_buf.append(line)
                if len(err_buf) > 40:
                    del err_buf[0]

    th = threading.Thread(target=read_err, daemon=True)
    th.start()

    # 看门狗：用户一按停止，立刻掐掉当前 ffmpeg，不再等这条素材跑完
    def watchdog():
        while p.poll() is None:
            if JOB["stopping"]:
                interrupted["v"] = True
                try:
                    p.terminate()
                    t0 = time.time()
                    while time.time() - t0 < 1.2:
                        if p.poll() is not None:
                            return
                        time.sleep(0.05)
                    p.kill()
                except Exception:
                    pass
                return
            time.sleep(0.15)

    threading.Thread(target=watchdog, daemon=True).start()

    for line in p.stdout:
        line = line.strip()
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k == "out_time_us" and duration > 0:
            try:
                cur = int(v) / 1e6
                pct = min(99.9, cur / duration * 100.0)
                with JOB_LOCK:
                    JOB["file_percent"] = round(pct, 1)
                    if JOB["total"]:
                        JOB["percent"] = round((JOB["done"] + pct / 100.0) / JOB["total"] * 100, 1)
            except Exception:
                pass
        elif k == "speed":
            with JOB_LOCK:
                JOB["speed"] = v

    p.wait()
    RUNNING_PROC["p"] = None
    th.join(timeout=2)

    try:
        if duration > 0 and JOB["total"]:
            el = time.time() - t_start
            fp = max(1.0, JOB["file_percent"])
            per_file = el / (fp / 100.0)
            left = (JOB["total"] - JOB["done"] - 1) * per_file + per_file * (1 - fp / 100.0)
            with JOB_LOCK:
                JOB["eta"] = fmt_dur(left)
    except Exception:
        pass

    return p.returncode == 0, "\n".join(err_buf), interrupted["v"]


# ---------------------------------------------------------------- HTTP 服务

class Handler(BaseHTTPRequestHandler):
    server_version = "LUTBatch/" + VERSION

    def log_message(self, fmt, *args):
        pass

    # ---- 响应辅助
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype="text/html; charset=utf-8", cache=False):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404, "Not Found")
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            self.send_header("Cache-Control", "max-age=300")
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    # ---- 路由
    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        if p in ("/", "/index.html"):
            return self._file(os.path.join(STATIC, "index.html"))
        if p in ("/help", "/help.html"):
            return self._file(os.path.join(STATIC, "help.html"))
        if p == "/api/env":
            return self._json(api_env())
        if p == "/api/config":
            return self._json(load_config())
        if p == "/api/lutfav":
            return self._json(load_fav())
        if p == "/api/luthide":
            return self._json({"hidden": load_hidden()})
        if p == "/api/job":
            with JOB_LOCK:
                snap = dict(JOB)
                snap["log"] = JOB["log"][-200:]
                snap["errors"] = list(JOB["errors"])
            return self._json(snap)
        if p == "/api/img":
            q = parse_qs(u.query)
            f = (q.get("f") or [""])[0]
            full = os.path.realpath(os.path.join(THUMBS, f))
            if not full.startswith(os.path.realpath(THUMBS)):
                return self.send_error(403)
            return self._file(full, "image/jpeg", cache=True)
        return self.send_error(404)

    def do_POST(self):
        u = urlparse(self.path)
        p = u.path
        d = self._body()

        if p == "/api/dialog":
            kind = d.get("kind", "input")
            prompt = {"input": "选择素材文件夹", "output": "选择导出文件夹",
                      "lut": "选择 LUT 文件夹"}.get(kind, "选择文件夹")
            path = pick_folder(prompt)
            return self._json({"path": path, "canceled": path is None})

        if p == "/api/scan_input":
            return self._scan_input(d)

        if p == "/api/scan_lut":
            folder = d.get("dir") or ""
            if not os.path.isdir(folder):
                return self._json({"error": "文件夹不存在"}, 400)
            luts = list_luts(folder)
            return self._json({"count": len(luts), "luts": luts[:1500]})

        if p == "/api/probe":
            path = d.get("path") or ""
            if not os.path.isfile(path):
                return self._json({"error": "文件不存在"}, 400)
            info = probe_cached(path)
            det = detect_log(info, path, sample=True)
            return self._json({"info": info, "log": det, "precheck": precheck(info)})

        if p == "/api/luthide":
            path = (d.get("path") or "").strip()
            if d.get("all") == "restore":
                save_hidden([])
                return self._json({"hidden": [], "ok": True})
            if not path:
                return self._json({"error": "缺少 path"}, 400)
            hid = load_hidden()
            if path in hid:
                hid.remove(path)
            else:
                hid.append(path)
            save_hidden(hid)
            return self._json({"hidden": sorted(set(hid)), "ok": True})

        if p == "/api/lutfav":
            fav = load_fav()
            path = d.get("path")
            if path is not None:
                if path in fav["favorites"]:
                    fav["favorites"].remove(path)
                else:
                    fav["favorites"].append(path)
            if d.get("favorites") is not None:
                fav["favorites"] = list(d["favorites"])
            save_json(FAV_PATH, fav)
            return self._json(fav)

        if p == "/api/start":
            with JOB_LOCK:
                if JOB["running"]:
                    return self._json({"error": "已有任务在跑"}, 400)
            if not d.get("input_dir") or not os.path.isdir(d["input_dir"]):
                return self._json({"error": "素材文件夹无效"}, 400)
            if not d.get("output_dir"):
                return self._json({"error": "请选择导出文件夹"}, 400)
            if not FFMPEG:
                return self._json({"error": "未找到 ffmpeg，请先安装（见说明书）"}, 400)
            os.makedirs(d["output_dir"], exist_ok=True)
            with JOB_LOCK:
                JOB.update({
                    "running": True, "stopping": False, "total": 0, "done": 0,
                    "failed": 0, "skipped": 0, "stopped": 0, "current": "", "percent": 0.0,
                    "file_percent": 0.0, "speed": "", "eta": "", "log": [],
                    "errors": [], "started_at": time.time(), "finished_at": 0,
                    "out_dir": d["output_dir"], "done_bytes": 0,
                    "report": "", "aspects": d.get("aspects", ["source"]),
                })
            save_config({
                "input_dir": d.get("input_dir"), "output_dir": d.get("output_dir"),
                "lut_dir": d.get("lut_dir"), "luts": d.get("luts", []),
                "format": d.get("format", "hevc"), "quality": d.get("quality", "normal"),
                "intensity": d.get("intensity", 1.0), "matrix": d.get("matrix", "auto"),
                "suffix": d.get("suffix", ""), "skip_existing": d.get("skip_existing", True),
                "aspects": d.get("aspects", ["source"]),
                "audio_mode": d.get("audio_mode", "keep"),
                "grade": d.get("grade", {}),
                "log_override": d.get("log_override", "auto"),
            })
            th = threading.Thread(target=run_job, args=(d,), daemon=True)
            th.start()
            return self._json({"ok": True})

        if p == "/api/stop":
            with JOB_LOCK:
                JOB["stopping"] = True
            # 立刻掐掉当前 ffmpeg，不等这条素材跑完（看门狗是兜底，这里是主动）
            stop_running_proc()
            return self._json({"ok": True})

        if p == "/api/open":
            folder = d.get("dir") or ""
            if os.path.isdir(folder):
                subprocess.Popen(["open", folder])
                return self._json({"ok": True})
            return self._json({"error": "文件夹不存在"}, 400)

        if p == "/api/reveal":
            path = d.get("path") or ""
            if os.path.exists(path):
                subprocess.Popen(["open", "-R", path])
                return self._json({"ok": True})
            return self._json({"error": "文件不存在"}, 400)

        if p == "/api/config":
            return self._json(load_config())

        if p == "/api/preview":
            return self._preview(d)

        return self.send_error(404)

    # ---- 素材扫描 + 体检
    def _scan_input(self, d):
        folder = d.get("dir") or ""
        if not os.path.isdir(folder):
            return self._json({"error": "文件夹不存在"}, 400)
        files = list_videos(folder)

        # 全部素材都体检一遍（首次慢一点，之后走缓存）
        counts = {"ok": 0, "warn": 0, "reject": 0}
        log_counts = {}
        warnings = []
        sampled = 0
        for f in files:
            info = probe_cached(f["path"])
            f["info"] = info
            f["bit_depth"] = info.get("bit_depth", 0)
            lv, note = precheck(info)
            counts[lv] = counts.get(lv, 0) + 1
            if note and lv == "warn" and len(warnings) < 3:
                warnings.append("%s：%s" % (f["name"], note))
            # 元数据没结论的才抽帧，且最多抽 6 条，剩下的按多数派归拢
            allow = sampled < 6
            det = detect_log_cached(f["path"], info, allow_sample=allow)
            if det.get("stats"):
                sampled += 1
            f["log_type"] = det["type"]
            log_counts[det["type"]] = log_counts.get(det["type"], 0) + 1

        _cache_save()

        sample = files[0]["info"] if files else {}
        summary = None
        if log_counts:
            total = len(files) or 1
            top, top_n = max(log_counts.items(), key=lambda kv: kv[1])
            breakdown = {LOG_TYPES.get(k, {"label": k})["label"]: v
                         for k, v in sorted(log_counts.items(), key=lambda kv: -kv[1])}
            if top_n / float(total) >= 0.6:
                # 大多数素材一致，采信它
                det = detect_log_cached(
                    files[0]["path"], sample) if files else {"type": top}
                if det.get("type") != top:
                    det = {"type": top,
                           "label": LOG_TYPES.get(top, {}).get("label", top),
                           "confidence": "inferred" if top in ("log_other", "unknown") else "metadata",
                           "reason": "%d 条素材中有 %d 条属于这一类" % (total, top_n)}
                summary = dict(det)
            else:
                summary = {"type": "mixed", "label": "素材类型不一致",
                           "confidence": "inferred",
                           "reason": "这一批里混了几种色彩曲线，建议分开处理，或手动指定"}
            summary["counts"] = breakdown

        return self._json({
            "count": len(files),
            "files": files[:600],
            "total_size": sum(f["size"] for f in files),
            "sample": sample,
            "log": summary,
            "precheck": {"counts": counts, "warnings": warnings},
        })

    # ---- 试色板
    def _preview(self, d):
        if not FFMPEG:
            return self._json({"error": "未找到 ffmpeg"}, 400)
        src = d.get("path") or ""
        luts = [x for x in (d.get("luts") or []) if x]
        if not os.path.isfile(src) or not luts:
            return self._json({"error": "参数不完整"}, 400)
        at = float(d.get("at") or 0)
        tag = "pv%d" % int(time.time())
        outdir = os.path.join(THUMBS, tag)
        os.makedirs(outdir, exist_ok=True)

        try:
            dur = probe_cached(src).get("duration") or 5
        except Exception:
            dur = 5
        if at <= 0:
            at = min(1.0, dur * 0.3)
        at = min(at, max(0.1, dur - 0.2))

        opts = {
            "matrix": d.get("matrix", "auto"),
            "intensity": float(d.get("intensity") or 1.0),
            "grade": d.get("grade") or {},
        }

        base = d.get("base") or ""
        staged = stage_luts(luts, tag)
        base_staged = None
        if base and os.path.isfile(base):
            base_staged = stage_luts([base], tag + "b")[0]
        results = []
        for i, lp in enumerate(staged):
            name = os.path.basename(luts[i]) if i < len(luts) else os.path.splitext(os.path.basename(lp))[0]
            name = os.path.splitext(name)[0]
            out = os.path.join(outdir, "p%03d.jpg" % i)
            chain = ([base_staged] if base_staged else []) + [lp]
            graph, label = build_graph(chain, opts, "yuvj420p",
                                       preview_width=520, with_params=False)
            cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                   "-ss", "%.2f" % at, "-i", src, "-filter_complex", graph,
                   "-map", "[%s]" % label, "-frames:v", "1", "-q:v", "4", out]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                if r.returncode == 0 and os.path.exists(out):
                    results.append({"name": name, "file": name,
                                    "url": "/api/img?f=%s/p%03d.jpg" % (tag, i)})
                else:
                    results.append({"name": name, "file": name, "url": None,
                                    "err": (r.stderr or "").strip().splitlines()[-1:] or ""})
            except Exception as e:
                results.append({"name": name, "file": name, "url": None, "err": str(e)})
        for lp in staged:
            try:
                os.remove(lp)
            except Exception:
                pass
        if base_staged:
            try:
                os.remove(base_staged)
            except Exception:
                pass
        return self._json({"ok": True, "at": round(at, 2), "items": results})


def api_env():
    ff_ver = ""
    if FFMPEG:
        try:
            ff_ver = subprocess.run([FFMPEG, "-version"], capture_output=True, text=True,
                                    timeout=20).stdout.splitlines()[0]
            ff_ver = ff_ver.replace("ffmpeg version ", "").split(" Copyright")[0]
        except Exception:
            pass
    encoders = ""
    try:
        encoders = subprocess.run([FFMPEG, "-hide_banner", "-encoders"],
                                  capture_output=True, text=True, timeout=20).stdout
    except Exception:
        pass
    return {
        "app": APP_NAME, "version": VERSION,
        "ffmpeg_ok": bool(FFMPEG), "ffmpeg": FFMPEG or "", "ffmpeg_version": ff_ver,
        "ffprobe_ok": bool(FFPROBE),
        "hevc_vt": "hevc_videotoolbox" in encoders,
        "h264_vt": "h264_videotoolbox" in encoders,
        "prores": "prores_ks" in encoders,
        "formats": [{"key": k, "label": v["label"], "note": v["note"], "ext": v["ext"]}
                    for k, v in ENCODERS.items()],
        "aspects": [{"key": k, "label": v["label"], "note": v["note"]}
                    for k, v in ASPECTS.items()],
        "audio_modes": [{"key": k, "label": v["label"], "note": v["note"]}
                        for k, v in AUDIO_MODES.items()],
        "records": RECORDS,
        "builtin_luts": builtin_lut_list(),
        "root": ROOT,
    }


# ---------------------------------------------------------------- 启动

def pick_port(start=8787, tries=30):
    import socket
    cands = []
    env = (os.environ.get("LUT_PORT") or "").strip()
    if env.isdigit():
        cands.append(int(env))          # 手动指定端口（LUT_PORT=8800）优先
    cands += [start + i for i in range(tries)]
    for port in cands:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 0


def cleanup_thumbs(keep=3):
    """只保留最近几次的试色板，避免临时图堆积。"""
    try:
        subs = [os.path.join(THUMBS, d) for d in os.listdir(THUMBS)
                if os.path.isdir(os.path.join(THUMBS, d))]
        subs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for p in subs[keep:]:
            shutil.rmtree(p, ignore_errors=True)
    except Exception:
        pass


def install_signal_handlers():
    def bye(sig, frm):
        proc = RUNNING_PROC.get("p")
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                for _ in range(20):
                    if proc.poll() is not None:
                        break
                    time.sleep(0.1)
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass
        os._exit(0)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(sig, bye)
        except Exception:
            pass


def watch_parent():
    """被原生 app 启动时，app 一旦退出（包括强制退出），自己也跟着退，不留后台进程。"""
    if os.environ.get("LUT_NO_BROWSER") != "1":
        return
    origin = os.getppid()

    def loop():
        while True:
            time.sleep(3)
            if os.getppid() != origin:
                proc = RUNNING_PROC.get("p")
                if proc and proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                os._exit(0)

    threading.Thread(target=loop, daemon=True).start()


def main():
    cleanup_thumbs()
    install_signal_handlers()
    watch_parent()
    port = pick_port()
    if not port:
        print("找不到可用端口，请关掉一些程序再试。")
        sys.exit(1)
    url = "http://127.0.0.1:%d" % port
    print("=" * 52)
    print("  %s v%s" % (APP_NAME, VERSION))
    print("  地址：%s" % url)
    print("LISTENING %s" % url)
    print("  ffmpeg：%s" % (FFMPEG or "未检测到！"))
    print("  关闭这个终端窗口 = 退出工具")
    print("  批处理在后台跑，关掉页面不会中断任务")
    print("=" * 52)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    if not os.environ.get("LUT_NO_BROWSER"):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()
