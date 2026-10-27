#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LUT 用途辨别：这个 .cube 是给 Log 灰片用的，还是给已经还原的 Rec.709 画面用的？

为什么不只看文件名：
    同一个「万能 LUT 包」，有的是 Log→709 转换（放在文件名里会写 Log），
    有的只是风格调色（名字叫「01-风光人像」）。光靠文件名，一改中文名就废了。

真正能分开它们的是曲线形状：
    Log 素材是「扁」的 —— 一大段动态范围被压进很窄的码值区间。
    所以 Log→709 转换 LUT 必须把反差「拉开」，中性轴斜率明显大于 1（通常 1.2~1.6）。
    风格 LUT 作用在已经还原好的画面上，只需要微调，中性轴斜率接近 1（0.9~1.15）。

    另外技术转换 LUT 一般把黑位压到 0，而胶片风格 LUT 喜欢把黑位抬起来（0.02~0.08）。

只读中性轴（r=g=b 的那 33 个点）+ 几个彩色采样点，一个 33³ 的 LUT 十毫秒内读完。
"""

import os
import re


# ---------------------------------------------------------------- 特征提取

# .cube 的文件头写法五花八门，实测踩过的坑：
#   ① 头和数据之间常有空行（Resolve 导出的就有一行），把空行当数据会直接读崩
#   ② 有的文件 LUT_1D_SIZE 和 LUT_3D_SIZE 都有（一维整形表 + 三维表），
#      一维表在前，得先过一遍整形再查三维表
#   ③ DOMAIN_MIN / DOMAIN_MAX 不一定是 0~1
#   ④ 注释行可能夹在数据中间
_HEADER_KEYS = ("TITLE", "LUT_3D_SIZE", "LUT_1D_SIZE", "DOMAIN_MIN", "DOMAIN_MAX",
                "LUT_3D_INPUT_RANGE", "LUT_1D_INPUT_RANGE")


def _parse_header(lines):
    """返回 (n3d, n1d, dmin, dmax, data_start)。头写得不规范时尽量兜住。"""
    n3d = n1d = 0
    dmin, dmax = 0.0, 1.0
    data_start = 0
    for idx, raw in enumerate(lines):
        s = raw.strip()
        if not s or s.startswith("#"):
            data_start = idx + 1
            continue
        up = s.upper()
        if up.startswith("TITLE"):
            data_start = idx + 1
            continue
        if up.startswith("LUT_3D_SIZE") or up.startswith("LUT_1D_SIZE"):
            parts = s.split()
            try:
                v = int(parts[1])
            except (IndexError, ValueError):
                return 0, 0, dmin, dmax, 0
            if up.startswith("LUT_3D_SIZE"):
                n3d = v
            else:
                n1d = v
            data_start = idx + 1
            continue
        if up.startswith("DOMAIN_MIN") or up.startswith("DOMAIN_MAX"):
            parts = s.split()
            if len(parts) >= 2:
                try:
                    v = float(parts[1])
                    if up.startswith("DOMAIN_MIN"):
                        dmin = v
                    else:
                        dmax = v
                except ValueError:
                    pass
            data_start = idx + 1
            continue
        if up.startswith("LUT_3D_INPUT_RANGE") or up.startswith("LUT_1D_INPUT_RANGE"):
            data_start = idx + 1
            continue
        data_start = idx          # 第一个数据行
        break
    return n3d, n1d, dmin, dmax, data_start


def _data_lines(lines, start):
    """丢掉空行和注释，只留数值行，顺序不变。"""
    out = []
    for raw in lines[start:]:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def read_cube_signature(path, sample_points=6):
    """
    读 .cube 的曲线特征。读不出来返回 None。

    返回：
        n           网格边长（33 / 65 …）
        black       输入 0 处的输出亮度
        white       输入 1 处的输出亮度
        mid_slope   中性轴中段（30%~70%）的斜率，1.0 = 不改变反差
        gray_bias   中性轴三通道最大偏差，正常 LUT 应该接近 0
        chroma      彩色采样点的饱和度倍率，>1 = 加饱和
    """
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception:
        return None

    n, n1d, dmin, dmax, start = _parse_header(lines)
    if n < 2 and n1d < 2:
        return None                      # 既没有三维表也没有一维表，不是能用的 .cube
    if n1d >= 2 and n < 2:
        return None                      # 纯一维 LUT，lut3d 吃不了，也不在我们处理范围

    data = _data_lines(lines, start)
    span = (dmax - dmin) or 1.0

    def to_float(s):
        parts = s.split()
        if len(parts) < 3:
            return None
        try:
            return (float(parts[0]), float(parts[1]), float(parts[2]))
        except ValueError:
            return None

    # ---- 一维整形表（如果有）：先建一条输入 → 输出的映射
    shaper = None
    if n1d >= 2:
        if len(data) < n1d + n * n * n:
            return None
        tbl = []
        for s in data[:n1d]:
            t = to_float(s)
            if t is None:
                return None
            tbl.append((t[0] - dmin) / span)
        data = data[n1d:]

        def shaper(x, _tbl=tbl):
            x = max(0.0, min(1.0, x))
            pos = x * (len(_tbl) - 1)
            i0 = int(pos)
            i1 = min(i0 + 1, len(_tbl) - 1)
            f = pos - i0
            return _tbl[i0] * (1 - f) + _tbl[i1] * f

    total = n * n * n
    if len(data) < total:
        return None

    def rgb_at(i):
        t = to_float(data[i])
        if t is None:
            return None
        if dmin != 0.0 or dmax != 1.0:
            t = tuple((c - dmin) / span for c in t)
        return t

    def lum(t):
        return 0.2126 * t[0] + 0.7152 * t[1] + 0.0722 * t[2]

    def line_of(ri, gi, bi):
        """R 变化最快 → 行号 = bi*N*N + gi*N + ri"""
        return bi * n * n + gi * n + ri

    # ---- 中性轴：r = g = b 的那 n 个网格点
    step = n * n + n + 1
    axis = []
    for i in range(n):
        t = rgb_at(i * step)
        if t is None:
            return None
        axis.append(lum(t))

    def axis_at(pos):
        """在网格之间线性插值，pos 是 0~1 的输入值。"""
        pos = max(0.0, min(1.0, pos)) * (n - 1)
        i0 = int(pos)
        i1 = min(i0 + 1, n - 1)
        f = pos - i0
        return axis[i0] * (1 - f) + axis[i1] * f

    # ---- 合成曲线：有整形表就先过整形，再查三维表
    K = max(33, n)
    curve = []
    for k in range(K):
        x = k / float(K - 1)
        s = shaper(x) if shaper else x
        curve.append(axis_at(s))

    black = curve[0]
    white = curve[-1]

    i1, i2 = int(round((K - 1) * 0.30)), int(round((K - 1) * 0.70))
    if i2 <= i1:
        return None
    mid_slope = (curve[i2] - curve[i1]) / ((i2 - i1) / float(K - 1))

    # 中性轴上三通道应该一致，偏了说明这个 LUT 会染色
    gray_bias = 0.0
    for i in (n // 4, n // 2, (3 * n) // 4):
        t = rgb_at(i * step)
        if t:
            gray_bias = max(gray_bias, max(t) - min(t))

    # ---- 彩色采样点：看饱和度被放大还是压缩
    ratios = []
    for (ri, gi, bi) in (
        (int(n * 0.60), int(n * 0.35), int(n * 0.35)),
        (int(n * 0.35), int(n * 0.60), int(n * 0.35)),
        (int(n * 0.35), int(n * 0.35), int(n * 0.60)),
        (int(n * 0.70), int(n * 0.50), int(n * 0.45)),
        (int(n * 0.50), int(n * 0.45), int(n * 0.70)),
        (int(n * 0.45), int(n * 0.70), int(n * 0.50)),
    )[:sample_points]:
        ri = max(0, min(n - 1, ri))
        gi = max(0, min(n - 1, gi))
        bi = max(0, min(n - 1, bi))
        t = rgb_at(line_of(ri, gi, bi))
        if t is None:
            continue
        in_chroma = max(ri, gi, bi) - min(ri, gi, bi)
        out_chroma = (max(t) - min(t)) * (n - 1)
        if in_chroma >= 2:
            ratios.append(out_chroma / float(in_chroma))
    chroma = sum(ratios) / len(ratios) if ratios else 1.0

    return {
        "n": n,
        "black": round(black, 5),
        "white": round(white, 5),
        "mid_slope": round(mid_slope, 4),
        "gray_bias": round(gray_bias, 5),
        "chroma": round(chroma, 4),
        "shaper": bool(shaper),
    }


# ---------------------------------------------------------------- 判定

LOG_TOKENS = (
    "applelog", "apple_log", "apple log", "log2", "log-2", "log_2", "log3", "log-3",
    "logtorec", "log_to_rec", "log-rec", "log→", "log to", "conversion", "convert",
    "cst", "technical", "transform", "input", "vlog", "slog", "clog", "flog",
    "hlg", "2084", "logc", "log",
)

DISPLAY_TOKENS = (
    "rec709", "rec.709", "rec_709", "709", "srgb", "display", "gamma2.2",
    "print", "filmic", "filmlook", "film emulation", "lut pack", "preset",
    "universal", "master", "final", "grade", "调色", "滤镜", "预设",
)

# 「Log → 709」这种名字，两个方向都沾，必须优先认出来：它的**输入**是 Log。
# 同时提到 Log 和 709 的，一定是在描述一次转换，输入必然是 Log。
# 只提 Rec.709 / sRGB / display 的，才是在说「给已还原的画面用」。
_LOG_STRONG = (
    "applelog", "apple_log", "apple log", "vlog", "v-log", "slog", "s-log",
    "clog", "c-log", "flog", "f-log", "logc", "dlog", "nlog", "redlog",
    "log2", "log3", "log-2", "log_2", "log-3", "log_3", "hlg", "2084",
    "cineon", "acesc", "aces", "lin2log",
)
_LOG_WORD = re.compile(r"(?<![a-z])log(?![a-z])", re.I)
_DISPLAY_STRONG = ("709", "srgb", "display", "gamma2.2", "p3")


def _mentions_log(n):
    for t in _LOG_STRONG:
        if t in n:
            return t
    m = _LOG_WORD.search(n)
    if m:
        return m.group(0)
    return None


def _filename_hint(filename):
    """文件名里的强信号。返回 ('log'|'709'|None, 命中词)"""
    n = (filename or "").lower()

    log_tok = _mentions_log(n)
    has_display = any(t in n for t in _DISPLAY_STRONG)

    # ① 同时提到 Log 和 709 → 这个 LUT 是「Log 转 709」，输入是 Log
    if log_tok and has_display:
        return "log", "%s→709" % log_tok

    # ② 只提到 Log → 转换型
    if log_tok:
        return "log", log_tok

    # ③ 只提到显示端 → 给已还原画面用的风格 LUT
    if has_display:
        for t in DISPLAY_TOKENS:
            if t in n:
                return "709", t
        return "709", "709"

    # ④ 其余看风格词（不改变「输入是什么」，只是弱线索，交给曲线判断）
    return None, ""


def classify(sig, filename=""):
    """
    综合曲线特征和文件名，判断这个 LUT 该喂什么素材。

    返回：
        domain      'log' / '709' / 'unknown'
        confidence  'filename'（文件名强信号） / 'curve'（曲线判断） / 'none'
        reason      给人看的一句话
    """
    hint, token = _filename_hint(filename)

    if sig is None:
        if hint:
            return {"domain": hint, "confidence": "filename",
                    "reason": "只从文件名「%s」看出来，读不出曲线" % token}
        return {"domain": "unknown", "confidence": "none", "reason": "读不出曲线，文件名也没有线索"}

    slope = sig["mid_slope"]
    black = sig["black"]

    if hint:
        return {"domain": hint, "confidence": "filename",
                "reason": "文件名含「%s」；中性轴斜率 %.2f，黑位 %.3f" % (token, slope, black)}

    # 纯靠曲线打分。分界线定在 1.25：
    #   实测 Apple Log→Rec.709 的斜率是 2.2（Log 太扁，必须大幅拉开）
    #   风格 LUT 一般 0.95~1.15（只微调），抬黑位的胶片味更低
    score = max(-2.0, min(2.0, (slope - 1.25) / 0.35))
    bits = []

    if slope >= 1.5:
        bits.append("反差大幅拉开")
    elif slope >= 1.2:
        bits.append("反差轻度拉开")
    elif slope >= 0.95:
        bits.append("反差基本不变")
    else:
        bits.append("反差被压平")

    # 抬黑位是胶片风格的典型手法；技术转换一般黑位到 0
    if black > 0.02:
        score -= 0.45
        bits.append("黑位抬到 %.3f" % black)
    elif black < 0.004:
        bits.append("黑位到 0")

    if score >= 0.4:
        domain = "log"
    elif score <= -0.4:
        domain = "709"
    else:
        domain = "unknown"

    return {
        "domain": domain,
        "confidence": "curve" if domain != "unknown" else "none",
        "reason": "、".join(bits) + "（斜率 %.2f）" % slope,
        "score": round(score, 3),
    }


# ---------------------------------------------------------------- 说明文案

DOMAIN_META = {
    "log": {
        "label": "Log 用",
        "short": "Log",
        "desc": "设计给未还原的 Log 灰片，直接套会拉开反差 —— 一般就是转换 / CST 型",
    },
    "709": {
        "label": "709 用",
        "short": "709",
        "desc": "设计给已经还原成 Rec.709 的画面，套在 Log 灰片上会发灰发脏，得先还原",
    },
    "unknown": {
        "label": "判不准",
        "short": "?",
        "desc": "曲线特征不明显，文件名也没有线索。建议先用试色板看一眼",
    },
}
