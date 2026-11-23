# LUT 批量导出器 · LUTBatch

给 iPhone 拍的 Apple Log / Log 2 灰片批量套 LUT 并导出。拖一个文件夹进来，选 LUT，点开始，去干别的。

macOS · 原生窗口 + 本地服务 + ffmpeg · MIT

**下载**：<https://github.com/js-ping/lut-batch-exporter/releases>

---

## 为什么需要它

网上买的「万能 LUT 包」几乎全是**风格 LUT**，它们是给已经调好色的 Rec.709 画面用的。
直接套在 Log 灰片上，画面一定发灰、发脏、肤色发橙 —— 不是 LUT 不好，是顺序少了第一步。

这个工具把顺序固定死：

| 槽位 | 作用 | 例子 |
|---|---|---|
| ① 转换 LUT | Log → Rec.709，始终 100% | `AppleLog2_to_Rec709.cube` |
| ② 风格 LUT | 叠在上面的创作风格，0–100% 可调 | 你的风格包 |

**没有转换 LUT 也能出片**：点「仅色彩还原」，用内置曲线（按 Apple 白皮书常量算出来的）还原。

## 功能

- 素材来源两种：选整个文件夹（含子文件夹），或挑单条文件（多选）；「清空」一键重来
- 素材曲线自动识别（Apple Log 2 / Apple Log / 其它 Log / 已还原），可手动指定
- 灰片没挂转换 LUT 时顶部拦截，不允许稀里糊涂导出
- LUT 库：收藏、最近用过、显示来源子文件夹、按曲线判定「Log 用 / 709 用」
- 多画幅一次输出（原画幅 / 16:9 / 9:16 / 1:1 / 4:5）
- 四种输出：ProRes 422 HQ / ProRes 422 / HEVC 10bit / H.264 8bit
- 音频四模式、画面微调（曝光 / 对比 / 饱和 / 色温）、导出 13 列 CSV 报告
- 时长预估：开始前告诉你要多久，跑起来告诉你还剩多久
- 停止即停（看门狗 0.15 秒掐断 ffmpeg，半成品自动删除）

## 安装

1. 下载 `LUTBatchExporter_v1.8.0_macOS.dmg`，双击打开，把 `LUT批量导出器` 拖进「应用程序」。
2. 需要 ffmpeg：`brew install ffmpeg`（没装的话 App 里会直接把这条命令摆给你）。
3. 从启动台打开。首次打开被系统拦（未公证）：右键 → 打开 → 再打开。

dmg 里的 `.app` 是**自包含**的（`程序/` 已打进 `Contents/Resources/`），单独拷到任何地方都能跑。
配置、导出记录、缓存写在 `~/Library/Application Support/LUT批量导出器/`，升级时覆盖 app 即可，数据不动。
菜单 **LUT 批量导出器 → 打开数据文件夹** 直达。

## 两种运行形态

同一个二进制，靠「`程序/` 在哪」自己判断：

| 形态 | `程序/` 位置 | 数据写哪 | 判断依据 |
|---|---|---|---|
| 安装版（dmg） | `LUT批量导出器.app/Contents/Resources/程序` | `~/Library/Application Support/LUT批量导出器/` | bundle 里有 `程序/server.py`，启动器传 `LUT_DATA_DIR` |
| 展开版（工具包） | 与 `.app` 并排 | 工具包根目录 + `程序/` | 上一级有 `启动.command` / `使用说明.html` / 同名 `.app` |

app bundle 是只读的（往里写还会破坏 ad-hoc 签名），所以安装版必须把数据外置。展开版的设置仍留在 `程序/`，老用户配置不用搬家。

## 自检

换电脑、升 ffmpeg 后先跑一遍：

```bash
python3 开发/自检/自检.py
```

93 项：环境、Log 识别、每种输出格式、音轨四模式、多画幅、微调、LUT 强度、试色板、报告、内置曲线、LUT 用途辨别、时长预估、安装版/展开版目录布局。

## 从源码构建

```bash
开发/native/构建app.command        # 只重编本机用的轻外壳 .app（程序/ 留在外面）
开发/打包dmg.command               # 打自包含 .app + 封 dmg（发版用）
双击 发布新版本.command            # 提交 → 打 tag → 推 GitHub → 建 Release → 传 dmg
```

## 已知限制

- 只支持 `.cube` / `.3dl` / `.dat` / `.m3d` / `.csp` 三维 LUT，不支持 Adobe `.look`
- 不做水印 / 字幕烧录（依赖 ffmpeg 的 `drawtext`，多数发行版没编译）
- 4K 素材约 0.3–0.5× 实时：三维 LUT 是纯 CPU 逐像素运算，ffmpeg 没有 GPU 版本

## 许可

MIT
