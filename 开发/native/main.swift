//  LUT 批量导出器 · 原生 macOS 外壳
//  一个真正的 Mac 应用窗口：自带 WebKit 视图 + 原生文件夹选择面板
//  不需要浏览器、不需要标签页，⌘Q 退出时连同后台服务一起结束

import Cocoa
import WebKit

// ---------------------------------------------------------------- 工具函数

func jsQuote(_ s: String) -> String {
    let esc = s.replacingOccurrences(of: "\\", with: "\\\\")
               .replacingOccurrences(of: "'", with: "\\'")
               .replacingOccurrences(of: "\n", with: " ")
    return "'" + esc + "'"
}

func findPython() -> String? {
    let candidates = ["/usr/bin/python3",
                      "/opt/homebrew/bin/python3",
                      "/usr/local/bin/python3"]
    for p in candidates where FileManager.default.isExecutableFile(atPath: p) {
        return p
    }
    return nil
}

// ---------------------------------------------------------------- AppDelegate

final class AppDelegate: NSObject, NSApplicationDelegate, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate {

    var window: NSWindow!
    var webView: WKWebView!
    var server: Process?
    var outPipe: Pipe?
    var errPipe: Pipe?
    var outBuf = ""
    var errBuf = ""
    var loaded = false
    var toolRoot: URL!
    var rootURL: URL?

    // ---- 启动
    func applicationDidFinishLaunching(_ note: Notification) {
        // .app 就放在工具包根目录下，上一级就是根
        toolRoot = Bundle.main.bundleURL.deletingLastPathComponent()

        buildMenu()
        buildWindow()
        startServer()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ note: Notification) {
        stopServer()
    }

    // ---- 菜单（必须建，否则输入框里的 ⌘V 用不了）
    func buildMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "关于 LUT 批量导出器", action: #selector(aboutMe), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "打开工具文件夹", action: #selector(openToolFolder), keyEquivalent: "")
        appMenu.addItem(withTitle: "打开导出记录", action: #selector(openRecords), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "隐藏", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "退出", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)

        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "编辑")
        editMenu.addItem(withTitle: "撤销", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "重做", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "拷贝", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu
        main.addItem(editItem)

        let viewItem = NSMenuItem()
        let viewMenu = NSMenu(title: "显示")
        viewMenu.addItem(withTitle: "返回主界面", action: #selector(goHome), keyEquivalent: "[")
        viewMenu.addItem(withTitle: "重新载入界面", action: #selector(reloadPage), keyEquivalent: "r")
        viewMenu.addItem(withTitle: "实际大小", action: #selector(zoomActual), keyEquivalent: "0")
        viewMenu.addItem(withTitle: "放大", action: #selector(zoomIn), keyEquivalent: "+")
        viewMenu.addItem(withTitle: "缩小", action: #selector(zoomOut), keyEquivalent: "-")
        viewItem.submenu = viewMenu
        main.addItem(viewItem)

        let winItem = NSMenuItem()
        let winMenu = NSMenu(title: "窗口")
        winMenu.addItem(withTitle: "最小化", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        winMenu.addItem(withTitle: "缩放", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        winItem.submenu = winMenu
        main.addItem(winItem)

        NSApp.mainMenu = main
    }

    // ---- 窗口
    func buildWindow() {
        let rect = NSRect(x: 0, y: 0, width: 1280, height: 900)
        window = NSWindow(contentRect: rect,
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "LUT 批量导出器"
        window.titlebarAppearsTransparent = false
        window.minSize = NSSize(width: 1000, height: 700)
        window.appearance = NSAppearance(named: .darkAqua)
        window.backgroundColor = NSColor(calibratedRed: 0.043, green: 0.051, blue: 0.063, alpha: 1)
        window.center()

        let conf = WKWebViewConfiguration()
        conf.userContentController.add(self, name: "pickFolder")
        conf.preferences.setValue(true, forKey: "developerExtrasEnabled")

        webView = WKWebView(frame: rect, configuration: conf)
        webView.navigationDelegate = self
        webView.uiDelegate = self          // 让 confirm/alert/prompt 能弹出原生对话框
        webView.setValue(false, forKey: "drawsBackground")
        webView.autoresizingMask = [.width, .height]
        window.contentView = webView

        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func showLoading(_ text: String) {
        let html = """
        <html><head><meta charset="utf-8"><style>
        html,body{height:100%;margin:0;background:#0b0d10;color:#8d97a6;
          font:14px -apple-system,"PingFang SC";display:flex;align-items:center;justify-content:center}
        .b{text-align:center}.s{margin-top:10px;font-size:12px;color:#5f6875}
        .d{width:26px;height:26px;margin:0 auto 14px;border:2px solid #242a33;
           border-top-color:#e0a35c;border-radius:50%;animation:r .8s linear infinite}
        @keyframes r{to{transform:rotate(360deg)}}
        </style></head><body><div class="b"><div class="d"></div>\(text)
        <div class="s">正在启动本地处理服务…</div></div></body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }

    // ---- 启动 Python 服务
    func startServer() {
        showLoading("正在唤醒")

        guard let py = findPython() else {
            alert("找不到 Python 3", "请先在终端执行：xcode-select --install")
            return
        }
        let serverPy = toolRoot.appendingPathComponent("程序/server.py").path
        guard FileManager.default.fileExists(atPath: serverPy) else {
            alert("工具文件缺失", "找不到程序/server.py，请把 .app 放回工具包文件夹里。")
            return
        }

        let task = Process()
        task.executableURL = URL(fileURLWithPath: py)
        task.arguments = ["-u", serverPy]
        var env = ProcessInfo.processInfo.environment
        env["LUT_NO_BROWSER"] = "1"
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        env["PYTHONUNBUFFERED"] = "1"
        task.environment = env

        let op = Pipe(); let ep = Pipe()
        task.standardOutput = op
        task.standardError = ep
        outPipe = op; errPipe = ep

        op.fileHandleForReading.readabilityHandler = { [weak self] h in
            let d = h.availableData
            guard let self = self, !d.isEmpty, let s = String(data: d, encoding: .utf8) else { return }
            DispatchQueue.main.async { self.handleOut(s) }
        }
        ep.fileHandleForReading.readabilityHandler = { [weak self] h in
            let d = h.availableData
            guard let self = self, !d.isEmpty, let s = String(data: d, encoding: .utf8) else { return }
            DispatchQueue.main.async { self.errBuf += s }
        }

        do {
            try task.run()
            server = task
        } catch {
            alert("启动失败", "无法启动本地服务：\(error.localizedDescription)")
        }
    }

    func handleOut(_ chunk: String) {
        outBuf += chunk
        if !loaded,
           let r = outBuf.range(of: "LISTENING http://127.0.0.1:") {
            let rest = outBuf[r.upperBound...]
            let digits = rest.prefix { $0.isNumber }
            if !digits.isEmpty, let url = URL(string: "http://127.0.0.1:\(digits)/") {
                loaded = true
                rootURL = url
                webView.load(URLRequest(url: url))
            }
        }
    }

    func stopServer() {
        outPipe?.fileHandleForReading.readabilityHandler = nil
        errPipe?.fileHandleForReading.readabilityHandler = nil
        if let s = server, s.isRunning {
            s.terminate()
            // 给 ffmpeg 子进程一点时间收尾
            let deadline = Date().addingTimeInterval(2.5)
            while s.isRunning && Date() < deadline { usleep(80000) }
            if s.isRunning { kill(s.processIdentifier, SIGKILL) }
        }
        server = nil
    }

    // ---- JS ⇄ 原生 桥：选文件夹
    func userContentController(_ ucc: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "pickFolder" else { return }
        let kind = (message.body as? String) ?? "input"
        DispatchQueue.main.async { self.openPanel(kind: kind) }
    }

    // ---- JS 里的 alert / confirm / prompt
    //     默认情况下 WKWebView 不实现这几个就会**静默返回 false**，
    //     页面里的 confirm() 全部变成「点了没反应」。必须自己接住。
    func webView(_ webView: WKWebView,
                 runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = "提示"
        alert.informativeText = message
        alert.addButton(withTitle: "好")
        if let w = window {
            alert.beginSheetModal(for: w) { _ in completionHandler() }
        } else {
            alert.runModal()
            completionHandler()
        }
    }

    func webView(_ webView: WKWebView,
                 runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = "请确认"
        alert.informativeText = message
        alert.addButton(withTitle: "继续")
        alert.addButton(withTitle: "取消")
        if let w = window {
            alert.beginSheetModal(for: w) { resp in
                completionHandler(resp == .alertFirstButtonReturn)
            }
        } else {
            completionHandler(alert.runModal() == .alertFirstButtonReturn)
        }
    }

    func webView(_ webView: WKWebView,
                 runJavaScriptTextInputPanelWithPrompt prompt: String,
                 defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (String?) -> Void) {
        let alert = NSAlert()
        alert.messageText = "输入"
        alert.informativeText = prompt
        alert.addButton(withTitle: "确定")
        alert.addButton(withTitle: "取消")
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 260, height: 24))
        field.stringValue = defaultText ?? ""
        alert.accessoryView = field
        let finish: (NSApplication.ModalResponse) -> Void = { resp in
            completionHandler(resp == .alertFirstButtonReturn ? field.stringValue : nil)
        }
        if let w = window {
            alert.beginSheetModal(for: w, completionHandler: finish)
        } else {
            finish(alert.runModal())
        }
    }

    func openPanel(kind: String) {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.canCreateDirectories = (kind == "output")
        panel.showsHiddenFiles = false

        switch kind {
        case "input":
            panel.message = "选择放着灰片的素材文件夹（子文件夹会一起处理）"
            panel.prompt = "选为素材"
        case "lut":
            panel.message = "选择放着 .cube 文件的 LUT 文件夹"
            panel.prompt = "选为 LUT"
        case "output":
            panel.message = "选择导出到哪个文件夹（不存在可以现场新建）"
            panel.prompt = "导出到这里"
        default:
            panel.prompt = "选择"
        }

        let resp = panel.runModal()
        let path = (resp == .OK) ? (panel.url?.path ?? "") : ""
        let js = "window.__nativePickResult(\(jsQuote(kind)), \(jsQuote(path)))"
        webView.evaluateJavaScript(js) { _, _ in }
    }

    // ---- 导航：外链丢给系统浏览器
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if action.targetFrame == nil, let host = action.request.url?.host,
           host == "127.0.0.1" || host == "localhost" {
            webView.load(action.request)
            decisionHandler(.cancel)
            return
        }
        if let url = action.request.url,
           let host = url.host,
           host != "127.0.0.1", host != "localhost" {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if !loaded { showLoading("本地服务没有响应") }
    }

    // ---- 菜单动作
    @objc func aboutMe() {
        let a = NSAlert()
        a.messageText = "LUT 批量导出器"
        a.informativeText = "Apple Log 2 灰片批量套 LUT 导出\n全程本地处理，不联网、不上传。\n\n底层：ffmpeg  ·  界面：WebKit\n日志与设置都在工具包文件夹里。"
        a.addButton(withTitle: "好")
        a.runModal()
    }

    @objc func openToolFolder() {
        NSWorkspace.shared.open(toolRoot)
    }

    @objc func openRecords() {
        let rec = toolRoot.appendingPathComponent("运行记录")
        NSWorkspace.shared.open(FileManager.default.fileExists(atPath: rec.path) ? rec : toolRoot)
    }

    @objc func goHome() {
        if let u = rootURL { webView.load(URLRequest(url: u)) }
    }

    @objc func reloadPage() {
        if loaded { webView.reload() }
    }

    @objc func zoomActual() { webView.pageZoom = 1.0 }
    @objc func zoomIn() { webView.pageZoom = min(2.0, webView.pageZoom + 0.1) }
    @objc func zoomOut() { webView.pageZoom = max(0.6, webView.pageZoom - 0.1) }

    func alert(_ title: String, _ msg: String) {
        let a = NSAlert()
        a.messageText = title
        a.informativeText = msg
        a.alertStyle = .warning
        a.addButton(withTitle: "好")
        a.runModal()
    }
}

// ---------------------------------------------------------------- 启动

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
