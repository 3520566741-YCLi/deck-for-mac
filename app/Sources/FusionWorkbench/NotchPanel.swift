import AppKit
import Carbon.HIToolbox
import WebKit

/// 刘海模式：没接 iPad 时，Deck 从 MacBook 刘海下面像抽屉一样弹出来。
///
/// 里面放的就是扩展屏那个 Deck 网页（/dash?app=1&notch=1），卡片、Vesper、设置、语音都是同一套；
/// 面板的形状和「水帘」展开动画也由网页画。这里只负责：
/// - 一个透明、不抢焦点的窗口贴在刘海正下方（大小跟着网页设置里的「刘海模式面板大小」）；
/// - 鼠标碰到刘海本身（只认刘海那一块，往下多 8 点）就叫网页展开；刘海两边的菜单栏图标照常能点；
///   离开面板 0.32 秒后收起；Vesper 正在听/想/说、正在打字时不收，说完 4 秒鼠标不在面板上再收；
/// - 收起时整个窗口不接鼠标，菜单栏照常点。
/// 刘海尺寸的算法参考 NotchDrop（MIT，Lakr233）和 DynamicNotchKit（MIT，MrKai77）：safeAreaInsets.top + auxiliaryTopLeft/RightArea。
final class NotchPanel: NSObject, WKScriptMessageHandler, WKNavigationDelegate, WKUIDelegate {
    private var window: NotchWindow?
    private var web: WKWebView?
    private var dashURL: URL?
    private(set) var active = false
    private(set) var isOpen = false
    private var hold = false
    private var size = CGSize(width: 840, height: 480)
    private let sideMargin: CGFloat = 36     // 两边和下面给阴影留的透明边
    private let bottomMargin: CGFloat = 56
    private var monitors: [Any] = []
    private var leaveTimer: Timer?
    private var idleTimer: Timer?
    private var retryTimer: Timer?
    private var geo = Geometry(screen: nil, centerX: 0, top: 0, notchWidth: 200, notchHeight: 37)

    struct Geometry: Equatable {
        var screen: NSScreen?
        var centerX: CGFloat
        var top: CGFloat
        var notchWidth: CGFloat
        var notchHeight: CGFloat
        static func == (a: Geometry, b: Geometry) -> Bool {
            a.screen == b.screen && a.centerX == b.centerX && a.top == b.top && a.notchWidth == b.notchWidth && a.notchHeight == b.notchHeight
        }
    }

    // MARK: - 开启 / 关闭刘海模式

    func activate(dash url: URL) {
        dashURL = url
        if !active {
            active = true
            buildIfNeeded()
            startMonitors()
        }
        refreshGeometry(force: true)
        load()
        window?.orderFrontRegardless()
    }

    func deactivate() {
        guard active else { return }
        active = false
        setOpen(false)
        stopMonitors()
        retryTimer?.invalidate(); retryTimer = nil
        window?.orderOut(nil)
        // 卸掉网页：同一时间只留一个 Deck 页面连着语音服务，免得 Vesper 回答两遍
        web?.load(URLRequest(url: URL(string: "about:blank")!))
    }

    func reload() { if active { load() } }

    /// 设置页「显示方式」的消息（deckMode）交给 AppDelegate 处理
    var onModeMessage: ((WKScriptMessage, WKWebView) -> Void)?

    func evaluate(_ js: String) { if active { web?.evaluateJavaScript(js, completionHandler: nil) } }

    /// 菜单栏「显示 Deck 面板」/ ⌥空格
    func toggle() {
        guard active else { return }
        setOpen(!isOpen)
        if isOpen { scheduleIdleClose() }
    }

    func screensChanged() {
        guard active else { return }
        refreshGeometry(force: false)
    }

    // MARK: - 窗口

    private func buildIfNeeded() {
        guard window == nil else { return }
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()  // 和扩展屏共用存储：卡片顺序、隐藏、设置都一样
        config.userContentController.add(WeakScriptHandler(self), name: "deckNotch")
        config.userContentController.add(WeakScriptHandler(self), name: "deckMode")  // 设置页「显示方式」，交给 AppDelegate
        let wv = NotchWebView(frame: .zero, configuration: config)
        wv.setValue(false, forKey: "drawsBackground")
        if #available(macOS 12.0, *) { wv.underPageBackgroundColor = .clear }
        wv.navigationDelegate = self
        wv.uiDelegate = self
        wv.autoresizingMask = [.width, .height]
        let w = NotchWindow(contentRect: NSRect(x: 0, y: 0, width: 912, height: 536),
                            styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        w.isOpaque = false
        w.backgroundColor = .clear
        w.hasShadow = false                 // 阴影由网页画（跟着水帘的形状）
        w.level = NSWindow.Level(rawValue: NSWindow.Level.statusBar.rawValue + 8)  // 盖在菜单栏上面，和刘海连成一片
        w.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]
        w.isMovable = false
        w.hidesOnDeactivate = false
        w.becomesKeyOnlyIfNeeded = true
        w.isReleasedWhenClosed = false
        w.ignoresMouseEvents = true
        w.animationBehavior = .none
        wv.frame = NSRect(origin: .zero, size: w.frame.size)
        w.contentView = wv
        window = w
        web = wv
    }

    private func load() {
        guard let dashURL, let web, var c = URLComponents(url: dashURL, resolvingAgainstBaseURL: false) else { return }
        c.queryItems = (c.queryItems ?? []).filter { !["notch", "nw", "nh", "pane"].contains($0.name) } + [
            URLQueryItem(name: "notch", value: "1"),
            URLQueryItem(name: "nw", value: String(Int(geo.notchWidth.rounded()))),
            URLQueryItem(name: "nh", value: String(Int(geo.notchHeight.rounded()))),
        ]
        c.fragment = nil
        if let u = c.url { web.load(URLRequest(url: u)) }
    }

    /// 带刘海的内建屏优先；合盖接外接显示器时退到主屏顶部正中（菜单栏高度当「刘海」高度）。
    private func notchScreen() -> NSScreen? {
        NSScreen.screens.first { $0.safeAreaInsets.top > 0 } ?? NSScreen.main ?? NSScreen.screens.first
    }

    private func refreshGeometry(force: Bool) {
        guard let s = notchScreen() else { return }
        var g = Geometry(screen: s, centerX: s.frame.midX, top: s.frame.maxY, notchWidth: 200,
                         notchHeight: max(24, s.frame.maxY - s.visibleFrame.maxY))
        if s.safeAreaInsets.top > 0, let l = s.auxiliaryTopLeftArea?.width, let r = s.auxiliaryTopRightArea?.width {
            g.notchWidth = max(120, s.frame.width - l - r)
            g.notchHeight = s.safeAreaInsets.top
            g.centerX = s.frame.minX + l + g.notchWidth / 2
        }
        let changed = g != geo
        geo = g
        placeWindow()
        if changed && !force {
            web?.evaluateJavaScript("window.__notch && window.__notch.geom(\(Int(g.notchWidth.rounded())), \(Int(g.notchHeight.rounded())))", completionHandler: nil)
        }
    }

    private func placeWindow() {
        guard let window else { return }
        let w = size.width + sideMargin * 2, h = size.height + bottomMargin
        let frame = NSRect(x: (geo.centerX - w / 2).rounded(), y: geo.top - h, width: w, height: h)
        if window.frame != frame { window.setFrame(frame, display: true) }
    }

    // MARK: - 鼠标感应

    private func startMonitors() {
        guard monitors.isEmpty else { return }
        let mask: NSEvent.EventTypeMask = [.mouseMoved, .leftMouseDragged]
        if let g = NSEvent.addGlobalMonitorForEvents(matching: mask, handler: { [weak self] _ in self?.mouseMoved() }) { monitors.append(g) }
        if let l = NSEvent.addLocalMonitorForEvents(matching: mask, handler: { [weak self] e in self?.mouseMoved(); return e }) { monitors.append(l) }
        // 点了别的地方：收起（正在和 Vesper 说话、正在打字时不收）
        if let c = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown], handler: { [weak self] _ in
            guard let self, self.isOpen, !self.hold, !self.inPanel(NSEvent.mouseLocation) else { return }
            self.setOpen(false)
        }) { monitors.append(c) }
    }

    private func stopMonitors() {
        monitors.forEach { NSEvent.removeMonitor($0) }
        monitors.removeAll()
        leaveTimer?.invalidate(); leaveTimer = nil
        idleTimer?.invalidate(); idleTimer = nil
    }

    private func onNotchScreen(_ p: NSPoint) -> Bool {
        guard let f = geo.screen?.frame else { return false }
        return p.x >= f.minX && p.x <= f.maxX && p.y >= f.minY && p.y <= f.maxY + 1
    }

    private func inHotZone(_ p: NSPoint) -> Bool {
        guard onNotchScreen(p) else { return false }
        // 只认刘海本身：以前左右各多 60 点、屏幕最上沿还多 ±360 点，鼠标去点刘海旁边的菜单栏图标（比如「«」）也会弹出来挡住
        let dx = abs(p.x - geo.centerX), fromTop = geo.top - p.y
        return fromTop <= geo.notchHeight + 8 && dx <= geo.notchWidth / 2 - 4
    }

    private func inPanel(_ p: NSPoint) -> Bool {
        abs(p.x - geo.centerX) <= size.width / 2 + 24 && geo.top - p.y <= size.height + 24 && p.y <= geo.top + 2
    }

    private func mouseMoved() {
        guard active else { return }
        let p = NSEvent.mouseLocation
        if !isOpen {
            if inHotZone(p) { setOpen(true) }
            return
        }
        if inPanel(p) || inHotZone(p) {
            leaveTimer?.invalidate(); leaveTimer = nil
            idleTimer?.invalidate(); idleTimer = nil
        } else if leaveTimer == nil {
            leaveTimer = Timer.scheduledTimer(withTimeInterval: 0.32, repeats: false) { [weak self] _ in
                guard let self else { return }
                self.leaveTimer = nil
                if !self.hold && !self.inPanel(NSEvent.mouseLocation) { self.setOpen(false) }
            }
        }
    }

    /// Vesper 说完 / 用快捷键打开后：4 秒内鼠标没进面板就收
    private func scheduleIdleClose() {
        idleTimer?.invalidate()
        idleTimer = Timer.scheduledTimer(withTimeInterval: 4, repeats: false) { [weak self] _ in
            guard let self else { return }
            self.idleTimer = nil
            if self.isOpen && !self.hold && !self.inPanel(NSEvent.mouseLocation) { self.setOpen(false) }
        }
    }

    private func setOpen(_ on: Bool) {
        guard let window, on != isOpen else { return }
        isOpen = on
        window.ignoresMouseEvents = !on
        if !on {
            leaveTimer?.invalidate(); leaveTimer = nil
            idleTimer?.invalidate(); idleTimer = nil
            // 收起时把键盘还给原来的应用
            if window.isKeyWindow { window.orderOut(nil); window.orderFrontRegardless() }
        }
        web?.evaluateJavaScript("window.__notch && window.__notch.set(\(on))", completionHandler: nil)
    }

    // MARK: - 网页 → 原生

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        if message.name == "deckMode" { if let web { onModeMessage?(message, web) }; return }
        // 只认监控台自己的主框架：网页卡片（iframe 里的第三方网站）不能开关面板
        let o = message.frameInfo.securityOrigin
        guard message.frameInfo.isMainFrame, o.protocol == "http", o.host == "127.0.0.1",
              let text = message.body as? String, text.count < 2000, let data = text.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let t = obj["t"] as? String else { return }
        switch t {
        case "ready", "size":
            let w = (obj["w"] as? NSNumber)?.doubleValue ?? 0, h = (obj["h"] as? NSNumber)?.doubleValue ?? 0
            if (400...1200).contains(w) && (200...800).contains(h) { size = CGSize(width: w, height: h); placeWindow() }
            if t == "ready" && isOpen { web?.evaluateJavaScript("window.__notch && window.__notch.set(true)", completionHandler: nil) }
        case "open":   // Vesper 被唤醒 / 按了 ←：抽屉自己流下来
            setOpen(true)
            if !inPanel(NSEvent.mouseLocation) { scheduleIdleClose() }
        case "close":
            setOpen(false)
        case "hold":
            hold = (obj["on"] as? Bool) ?? false
            if !hold && isOpen && !inPanel(NSEvent.mouseLocation) { scheduleIdleClose() }
            if hold { idleTimer?.invalidate(); idleTimer = nil }
        default: break
        }
    }

    // MARK: - 网页导航（和主窗口同样的规矩）

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { return decisionHandler(.cancel) }
        let isDash = url.host == "127.0.0.1" && url.path.hasPrefix("/dash")
        if isDash || url.absoluteString == "about:blank" || navigationAction.targetFrame?.isMainFrame == false {
            return decisionHandler(.allow)
        }
        if ["http", "https"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url, ["http", "https"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
        return nil
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        guard active else { return }
        retryTimer?.invalidate()
        retryTimer = Timer.scheduledTimer(withTimeInterval: 10, repeats: false) { [weak self] _ in self?.reload() }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { if active { load() } }
}

/// 面板不抢焦点，第一下点击也要直接生效（否则第一下只是让面板拿到焦点，按钮没反应）。
final class NotchWebView: WKWebView {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

/// 刘海面板窗口：不激活 Deck（不打断你正在用的应用），点进去才接键盘（和 Vesper 打字用）。
final class NotchWindow: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

/// ⌥空格：呼出 / 收起 Deck（Carbon 全局快捷键，不需要辅助功能权限；被别的应用占用时注册失败，什么也不发生）。
final class HotKey {
    private var ref: EventHotKeyRef?
    private var handler: EventHandlerRef?
    private let action: () -> Void
    private static var current: HotKey?

    init?(keyCode: UInt32 = UInt32(kVK_Space), modifiers: UInt32 = UInt32(optionKey), action: @escaping () -> Void) {
        self.action = action
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let status = InstallEventHandler(GetApplicationEventTarget(), { _, _, _ in
            DispatchQueue.main.async { HotKey.current?.action() }
            return noErr
        }, 1, &spec, nil, &handler)
        guard status == noErr else { return nil }
        let id = EventHotKeyID(signature: OSType(0x44454B48), id: 1)  // 'DEKH'
        guard RegisterEventHotKey(keyCode, modifiers, id, GetApplicationEventTarget(), 0, &ref) == noErr else {
            if let handler { RemoveEventHandler(handler) }
            return nil
        }
        HotKey.current = self
    }

    deinit {
        if let ref { UnregisterEventHotKey(ref) }
        if let handler { RemoveEventHandler(handler) }
    }
}
