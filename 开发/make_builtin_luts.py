#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成内置的 Apple Log / Apple Log 2 → Rec.709 转换 LUT。

为什么要有这个：
    网上买的「万能 LUT 包」几乎全是风格 LUT，不能直接套在灰片上。
    这个脚本按 Apple 白皮书公开的曲线常量 + 色域原色，现场算出一条
    纯数学的转换 LUT，这样没有转换 LUT 也能把灰片还原成正常画面。

数据来源（两份独立资料互相核对一致）：
    - Apple Log Profile White Paper (2023-09) / Apple Log 2 White Paper (2025-09)
      https://developer.apple.com/download/all/?q=Apple%20log%20profile
    - Antler Post「Apple Log Profile」页面 + colour-science 库
      CONSTANTS_APPLE_LOG_PROFILE
    曲线关键点：encode(0.18) = 0.4882724… 与白皮书「18% 灰落在 10bit 500」吻合。

重要事实：
    Apple Log 和 Apple Log 2 **用的是同一条 gamma 曲线**，区别只在色域：
        Apple Log    → Rec.2020 原色，D65
        Apple Log 2  → Apple Wide Gamut 原色，D65
    所以两者的转换 LUT 矩阵不同、曲线部分完全一样。

用法：
    python3 开发/make_builtin_luts.py
    会在 程序/luts/ 下生成两个 .cube 文件。
    改了常量想重新生成，重跑这个脚本即可。
"""

import math
import os
import sys

# ---------------------------------------------------------------- Apple Log 曲线

R0 = -0.05641088
Rt = 0.01
C = 47.28711236
BETA = 0.00964052
GAMMA = 0.08550479
DELTA = 0.69336945
PT = C * (Rt - R0) ** 2          # 线性段与 log 段的交界值


def apple_log_decode(p):
    """码值（0..1）→ 场景线性反射率。Apple 白皮书解码公式。"""
    if p < 0.0:
        return R0
    if p < PT:
        return math.sqrt(p / C) + R0
    return 2.0 ** ((p - DELTA) / GAMMA) - BETA


def apple_log_encode(l):
    """场景线性反射率 → 码值（0..1）。反函数，用于自检。"""
    if l < R0:
        return 0.0
    if l < Rt:
        return C * (l - R0) ** 2
    return GAMMA * math.log2(l + BETA) + DELTA


# ---------------------------------------------------------------- 色域

D65 = (0.3127, 0.3290)

GAMUTS = {
    # Apple Log（1 代）：Rec.2020 原色
    "apple_log": {
        "label": "Apple Log（1 代）",
        "primaries": [(0.708, 0.292), (0.170, 0.797), (0.131, 0.046)],
        "source": "ITU-R BT.2020-2",
    },
    # Apple Log 2：Apple Wide Gamut 原色（来自 Apple Log 2 白皮书）
    "apple_log_2": {
        "label": "Apple Log 2",
        "primaries": [(0.725, 0.301), (0.221, 0.814), (0.068, -0.076)],
        "source": "Apple Log 2 White Paper（经 OpenColorIO / Antler Post 核对）",
    },
    # 输出目标
    "_rec709": {
        "label": "Rec.709",
        "primaries": [(0.640, 0.330), (0.300, 0.600), (0.150, 0.060)],
        "source": "ITU-R BT.709-6",
    },
}


def xy_to_XYZ(x, y):
    return [x / y, 1.0, (1.0 - x - y) / y]


def rgb_to_xyz_matrix(primaries, white):
    """SMPTE RP 177：由原色色度坐标 + 白点算出 RGB→XYZ 矩阵。"""
    cols = [xy_to_XYZ(*p) for p in primaries]
    # 3x3，列是三个原色的 XYZ
    m = [[cols[0][i], cols[1][i], cols[2][i]] for i in range(3)]
    w = xy_to_XYZ(*white)
    s = solve3(m, w)
    return [[m[i][j] * s[j] for j in range(3)] for i in range(3)]


def solve3(a, b):
    """3x3 线性方程组求解（克拉默法则），避免依赖 numpy。"""
    det = det3(a)
    if abs(det) < 1e-12:
        raise ValueError("矩阵奇异，原色参数有问题")
    out = []
    for k in range(3):
        ak = [row[:] for row in a]
        for i in range(3):
            ak[i][k] = b[i]
        out.append(det3(ak) / det)
    return out


def det3(m):
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def inv3(m):
    d = det3(m)
    if abs(d) < 1e-12:
        raise ValueError("矩阵不可逆")
    c = [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        for j in range(3):
            # 余子式
            rows = [r for r in range(3) if r != i]
            cols = [k for k in range(3) if k != j]
            minor = (m[rows[0]][cols[0]] * m[rows[1]][cols[1]]
                     - m[rows[0]][cols[1]] * m[rows[1]][cols[0]])
            c[j][i] = ((-1) ** (i + j)) * minor / d     # 转置同时写入
    return c


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def mat_vec(m, v):
    return [sum(m[i][k] * v[k] for k in range(3)) for i in range(3)]


def build_matrix(src_key):
    src = rgb_to_xyz_matrix(GAMUTS[src_key]["primaries"], D65)
    dst = rgb_to_xyz_matrix(GAMUTS["_rec709"]["primaries"], D65)
    return mat_mul(inv3(dst), src)          # 源线性 RGB → 709 线性 RGB


# ---------------------------------------------------------------- Rec.709 编码

def oetf_709(l):
    """线性光 → Rec.709 码值（ITU-R BT.709 分段函数）。"""
    if l <= 0.0:
        return 0.0
    if l < 0.018:
        return 4.5 * l
    return 1.099 * (l ** 0.45) - 0.099


# ---------------------------------------------------------------- 生成 cube

def build_cube(src_key, size, path, clamp=True):
    """按 size³ 网格算出整条 LUT 并写成 .cube。"""
    m = build_matrix(src_key)
    half = (size - 1) / 2.0
    lines = []
    lines.append("TITLE \"%s to Rec.709 (builtin)\"" % GAMUTS[src_key]["label"])
    lines.append("# 由「LUT 批量导出器」按 Apple 公开曲线常量现场计算生成")
    lines.append("# 曲线：R0=%.8f Rt=%.2f c=%.8f beta=%.8f gamma=%.8f delta=%.8f"
                 % (R0, Rt, C, BETA, GAMMA, DELTA))
    lines.append("# 源色域：%s" % GAMUTS[src_key]["source"])
    lines.append("LUT_3D_SIZE %d" % size)
    lines.append("DOMAIN_MIN 0.0 0.0 0.0")
    lines.append("DOMAIN_MAX 1.0 1.0 1.0")

    for bi in range(size):
        b = bi / (size - 1.0)
        # .cube 的顺序是 R 变化最快，其次 G，最后 B
        for gi in range(size):
            g = gi / (size - 1.0)
            for ri in range(size):
                r = ri / (size - 1.0)
                lin = [apple_log_decode(r), apple_log_decode(g), apple_log_decode(b)]
                rgb709 = mat_vec(m, lin)
                out = []
                for v in rgb709:
                    if clamp:
                        v = 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)
                    out.append(oetf_709(v))
                lines.append("%.6f %.6f %.6f" % (out[0], out[1], out[2]))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(root, "程序", "luts")
    os.makedirs(out_dir, exist_ok=True)

    # ---- 先自检曲线，避免算错了还生成一堆文件
    errors = []
    checks = [
        ("18% 灰编码应为 0.488272", abs(apple_log_encode(0.18) - 0.48827245852686763) < 1e-6),
        ("解码回 18% 灰", abs(apple_log_decode(0.48827245852686763) - 0.18) < 1e-6),
        # 白皮书常量本身是舍入值，两段在 Rt 处差 4e-5 属正常
        ("曲线在 Rt 处连续（容差 1e-4）", abs(apple_log_encode(Rt) - PT) < 1e-4),
        # 码值 1.0 对应 1200% 场景反射率，是白皮书写的裁切点
        ("码值 1.0 应解出约 12.0（1200% 反射率）", abs(apple_log_decode(1.0) - 12.0) < 0.1),
        # 苹果的抛物线段就是用来装负值的，码值 0 对应 R0
        ("码值 0 应解出 R0（负值，保留黑电平以下）", abs(apple_log_decode(0.0) - R0) < 1e-9),
    ]
    for name, ok in checks:
        if not ok:
            errors.append(name)
    if errors:
        print("曲线自检失败：")
        for e in errors:
            print("  ✗", e)
        return 1
    print("✓ 曲线自检通过：encode(0.18) = %.7f" % apple_log_encode(0.18))
    print("  码值 1.0 解出线性值 %.2f（即约 %.0f%% 反射率）"
          % (apple_log_decode(1.0), apple_log_decode(1.0) * 100))

    # ---- 矩阵自检：中性灰不该偏色
    for key in ("apple_log", "apple_log_2"):
        m = build_matrix(key)
        gray = apple_log_decode(0.48827245852686763)
        out = mat_vec(m, [gray, gray, gray])
        spread = max(out) - min(out)
        if spread > 1e-6:
            print("✗ %s 中性灰出现偏色（通道差 %.2e）" % (key, spread))
            return 1
        print("✓ %s：中性灰三通道一致（差 %.1e）" % (GAMUTS[key]["label"], spread))

    # ---- 生成
    size = 33
    targets = [
        ("apple_log_2", "AppleLog2_to_Rec709.cube"),
        ("apple_log", "AppleLog_to_Rec709.cube"),
    ]
    for key, name in targets:
        p = os.path.join(out_dir, name)
        build_cube(key, size, p)
        print("✓ 已生成 %s（%d³，%.0f KB）" % (name, size, os.path.getsize(p) / 1024.0))

    print("")
    print("输出目录：%s" % out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
