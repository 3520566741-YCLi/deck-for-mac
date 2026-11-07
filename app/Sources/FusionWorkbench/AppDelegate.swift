import AppKit
import ServiceManagement
import WebKit

/// Deck（原 Fusion Workbench 2）：副屏监控台。
/// 数据由 Mac 管家的本地服务（127.0.0.1:8777/dash）采集；这个应用负责：
/// 自动拉起服务、把监控台放到接线的 iPad 扩展屏上（按屏幕名字找，铺满可见区域）、屏幕插拔后自动归位。
/// 窗口是普通窗口（有红黄绿按钮、层级 normal），关掉窗口应用仍留在菜单栏。
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    private var window: NSWindow!
    private var webView: DeckWebView!
    /// 红黄绿按钮中心离窗口顶部的距离（页面顶栏 64px 的正中）
    private let lightsCenterY: CGFloat = 32
    private let lightsLeft: CGFloat = 20
    private var statusItem: NSStatusItem!
    private let butler = ButlerService()
    private let errorView = ErrorOverlay()
    private var retryTimer: Timer?
    /// 助手对话面板的系统液态玻璃（ChatGlass，macOS 26+；更早的系统用网页自己画的面板）
    private var chatGlass: AnyObject?

    private let screenKey = "targetScreenName"

    /// 显示方式：auto（插着 iPad 用扩展屏，拔掉用刘海）/ ext（扩展屏）/ notch（刘海）
    private let modeKey = "displayMode"
    private let notch = NotchPanel()
    private var hotKey: HotKey?
    private var dashURL: URL?
    private var displayMode: String {
        let m = UserDefaults.standard.string(forKey: modeKey) ?? "auto"
        return ["auto", "ext", "notch"].contains(m) ? m : "auto"
    }
    /// 现在该用刘海吗
    private var useNotch: Bool {
        switch displayMode {
        case "notch": return true
        case "ext": return false
        default:
            // 自动：接着 iPad → 扩展屏；手动选过的外接屏接着（不是带刘海的内建屏）→ 扩展屏；其它 → 刘海
            if iPadScreen() != nil { return false }
            if let name = UserDefaults.standard.string(forKey: screenKey),
               let s = NSScreen.screens.first(where: { $0.localizedName == name }), s.safeAreaInsets.top == 0 { return false }
            return true
        }
    }
    private var notchShown = false

    // MARK: - 启动

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMainMenu()
        buildWindow()
        buildStatusItem()
        NotificationCenter.default.addObserver(self, selector: #selector(screensChanged),
                                               name: NSApplication.didChangeScreenParametersNotification, object: nil)
        hotKey = HotKey { [weak self] in self?.hotKeyPressed() }
        if useNotch { notchShown = true } else { placeWindow(); window.makeKeyAndOrderFront(nil) }
        connect()
    }

    /// 点 Dock 图标：窗口关了就重新打开
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if useNotch { notch.toggle() } else { showWindow() }
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    private func connect() {
        errorView.show(title: "正在连接 Mac 管家…", detail: "第一次启动要几秒钟。", showRetry: false, in: window.contentView!)
        butler.ensureRunning { [weak self] result in
            DispatchQueue.main.async { self?.handle(result) }
        }
    }

    private func handle(_ result: ButlerService.Result) {
        switch result {
        case .ready(let url):
            dashURL = url
            applyMode(force: true)
        case .failed(let message):
            errorView.show(title: "连不上 Mac 管家", detail: message, showRetry: true, in: window.contentView!)
            scheduleRetry()
        }
    }

    private func scheduleRetry() {
        retryTimer?.invalidate()
        retryTimer = Timer.scheduledTimer(withTimeInterval: 15, repeats: false) { [weak self] _ in self?.connect() }
    }

    // MARK: - 窗口

    private func buildWindow() {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        // 页面把顶栏的高度和按钮位置报过来（deckTitlebar），用来决定哪里可以拖窗口
        config.userContentController.add(WeakScriptHandler(self), name: "deckTitlebar")
        webView = DeckWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.setValue(false, forKey: "drawsBackground")
        webView.allowsMagnification = true

        // 普通带红黄绿按钮的窗口，层级 .normal：不压在别的应用上面。
        // fullSizeContentView：网页铺到标题栏下面，和页面顶栏融成一体；红黄绿挪到顶栏正中，页面顶栏左边给它们让位。
        window = DeckWindow(contentRect: NSRect(x: 0, y: 0, width: 1100, height: 800),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.title = "Deck"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.backgroundColor = NSColor(red: 0x0b / 255.0, green: 0x0d / 255.0, blue: 0x12 / 255.0, alpha: 1)
        window.level = .normal
        window.collectionBehavior = [.fullScreenPrimary, .managed]
        window.isReleasedWhenClosed = false  // 点红灯只关窗口，应用留在菜单栏
        window.delegate = self
        webView.autoresizingMask = [.width, .height]
        let container = NSView(frame: NSRect(origin: .zero, size: window.frame.size))
        container.autoresizingMask = [.width, .height]
        webView.frame = container.bounds
        container.addSubview(webView)
        window.contentView = container
        if #available(macOS 26.0, *) {
            let g = ChatGlass(main: webView, delegate: self)
            g.attach(above: webView)
            chatGlass = g
        }
        errorView.onRetry = { [weak self] in self?.connect() }
        // 旧版「盖住菜单栏 / 无边框铺满」留下的设置，清掉
        UserDefaults.standard.removeObject(forKey: "coverMenuBar")
        UserDefaults.standard.removeObject(forKey: "fillScreen")
    }

    /// 接线的 iPad：按名字找（OpenDisplay 显示为「OpenDisplay — iPad」），不按坐标或排序猜。
    private func iPadScreen() -> NSScreen? {
        NSScreen.screens.first { $0.localizedName.localizedCaseInsensitiveContains("iPad")
            || $0.localizedName.localizedCaseInsensitiveContains("OpenDisplay") }
    }

    /// 目标屏幕：菜单里手动选过的那块（按名字，且现在接着）> iPad > 主屏。
    private func targetScreen() -> NSScreen? {
        if let name = UserDefaults.standard.string(forKey: screenKey),
           let s = NSScreen.screens.first(where: { $0.localizedName == name }) { return s }
        return iPadScreen() ?? NSScreen.screens.first
    }

    /// 放窗口：在 iPad（或手动选的扩展屏）上是「最大化」的普通窗口（铺满可见区域，不盖菜单栏）；
    /// 在主屏上是居中的普通窗口。从不自动进系统全屏——要全屏点绿灯（单独的桌面空间，不会压着别的应用）。
    private func placeWindow() {
        defer { rebuildStatusMenu() }
        guard let screen = targetScreen(), !window.styleMask.contains(.fullScreen) else { return }
        window.level = .normal
        if screen != NSScreen.screens.first {
            window.setFrame(screen.visibleFrame, display: true, animate: false)
        } else {
            let f = screen.visibleFrame
            let w = min(1100, f.width - 40), h = min(800, f.height - 40)
            window.setFrame(NSRect(x: f.midX - w / 2, y: f.midY - h / 2, width: w, height: h), display: true)
        }
    }

    @objc private func screensChanged() {
        // 插拔屏幕 / OpenDisplay 重连后系统会挪窗口；稍等屏幕列表稳定再放回去（自动模式下顺便切扩展屏 / 刘海）
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
            guard let self else { return }
            self.applyMode(force: false)
            if self.useNotch { self.notch.screensChanged() } else { self.placeWindow() }
        }
    }

    // MARK: - 扩展屏 / 刘海

    /// 同一时间只开一个 Deck 页面（都连着语音服务，两个都开 Vesper 会答两遍）：
    /// 刘海模式时主窗口收起、网页卸掉；扩展屏模式时刘海面板卸掉。
    private func applyMode(force: Bool) {
        guard let url = dashURL else { return }
        let notchNow = useNotch
        if notchNow == notchShown && !force { rebuildStatusMenu(); return }
        notchShown = notchNow
        if notchNow {
            window.orderOut(nil)
            webView.load(URLRequest(url: URL(string: "about:blank")!))
            if #available(macOS 26.0, *), let g = chatGlass as? ChatGlass { g.unload() }
            notch.activate(dash: url)
        } else {
            notch.deactivate()
            webView.load(URLRequest(url: url))
            if #available(macOS 26.0, *), let g = chatGlass as? ChatGlass { g.load(dash: url) }
            placeWindow()
            window.makeKeyAndOrderFront(nil)
        }
        rebuildStatusMenu()
    }

    @objc private func chooseMode(_ sender: NSMenuItem) {
        guard let m = sender.representedObject as? String else { return }
        UserDefaults.standard.set(m, forKey: modeKey)
        applyMode(force: false)
        if useNotch { notch.toggle() }  // 切到刘海：先弹出来给你看一眼，4 秒后自己收
    }

    private func hotKeyPressed() {
        if useNotch { notch.toggle(); return }
        if window.isVisible && NSApp.isActive { NSApp.hide(nil) } else { showWindow() }
    }

    // MARK: - 菜单栏图标

    private func buildStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = NSImage(systemSymbolName: "gauge.with.dots.needle.67percent", accessibilityDescription: "Deck")
        rebuildStatusMenu()
    }

    private func rebuildStatusMenu() {
        guard statusItem != nil else { return }
        let menu = NSMenu()
        let show = item(useNotch ? "显示 Deck 面板" : "显示 Deck", #selector(showWindow))
        menu.addItem(show)
        menu.addItem(item("刷新", #selector(reload), key: "r"))
        menu.addItem(.separator())
        let head = NSMenuItem(title: "显示方式", action: nil, keyEquivalent: "")
        head.isEnabled = false
        menu.addItem(head)
        for (m, title, tip) in [("auto", "自动", "插着 iPad 用扩展屏，拔掉就用刘海"),
                                ("ext", "扩展屏模式", "铺满 iPad，刘海不弹出"),
                                ("notch", "刘海模式", "鼠标碰到刘海就从顶上弹出")] {
            let mi = item(title, #selector(chooseMode(_:)))
            mi.representedObject = m
            mi.state = displayMode == m ? .on : .off
            mi.toolTip = tip
            if #available(macOS 14.4, *) { mi.subtitle = tip }
            mi.indentationLevel = 1
            menu.addItem(mi)
        }
        menu.addItem(.separator())
        let screensItem = NSMenuItem(title: "显示在哪块屏幕", action: nil, keyEquivalent: "")
        let sub = NSMenu()
        let current = targetScreen()
        for s in NSScreen.screens {
            let mi = item(s.localizedName, #selector(chooseScreen(_:)))
            mi.representedObject = s.localizedName
            mi.state = s == current ? .on : .off
            sub.addItem(mi)
        }
        screensItem.submenu = sub
        menu.addItem(screensItem)
        menu.addItem(item(window.styleMask.contains(.fullScreen) ? "退出全屏" : "进入全屏", #selector(toggleFull)))
        let login = item("开机时自动打开", #selector(toggleLogin))
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        menu.addItem(login)
        menu.addItem(.separator())
        menu.addItem(item("打开 Mac 管家", #selector(openButler)))
        menu.addItem(item("退出", #selector(quit), key: "q"))
        statusItem.menu = menu
    }

    private func item(_ title: String, _ action: Selector, key: String = "") -> NSMenuItem {
        let mi = NSMenuItem(title: title, action: action, keyEquivalent: key)
        mi.target = self
        return mi
    }

    private func buildMainMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "刷新", action: #selector(reload), keyEquivalent: "r").target = self
        appMenu.addItem(withTitle: "隐藏 Deck", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "退出 Deck", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)
        // 窗口菜单：⌘W 关窗口（应用留在菜单栏），⌘M 最小化，⌃⌘F 系统全屏（和绿灯一样）
        let winItem = NSMenuItem()
        let winMenu = NSMenu(title: "窗口")
        winMenu.addItem(withTitle: "关闭窗口", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        winMenu.addItem(withTitle: "最小化", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        let fs = NSMenuItem(title: "进入全屏", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        fs.keyEquivalentModifierMask = [.control, .command]
        winMenu.addItem(fs)
        winItem.submenu = winMenu
        main.addItem(winItem)
        NSApp.mainMenu = main
        NSApp.windowsMenu = winMenu
    }

    @objc private func showWindow() {
        if useNotch { notch.toggle(); return }
        NSApp.activate(ignoringOtherApps: true)
        if window.isMiniaturized { window.deminiaturize(nil) }
        window.makeKeyAndOrderFront(nil)
        placeWindow()
    }
    @objc private func reload() {
        if dashURL == nil { connect() } else if useNotch { notch.reload() } else { webView.reload() }
    }
    @objc private func quit() { NSApp.terminate(nil) }
    /// 在监控台窗口里切到「Mac 管家」页，不再跳浏览器。
    @objc private func openButler() {
        if useNotch {  // 刘海里放不下管家页：换到浏览器打开
            NSWorkspace.shared.open(butler.baseURL); return
        }
        showWindow()
        if let url = URL(string: butler.dashURL.absoluteString + "#butler") { webView.load(URLRequest(url: url)) }
    }

    @objc private func chooseScreen(_ sender: NSMenuItem) {
        UserDefaults.standard.set(sender.representedObject as? String, forKey: screenKey)
        // 手动选了某块屏幕：就用扩展屏方式显示在那块屏上
        if displayMode == "notch" { UserDefaults.standard.set("ext", forKey: modeKey) }
        applyMode(force: false)
        placeWindow()
    }

    @objc private func toggleFull() {
        if !window.isVisible { showWindow() }
        window.toggleFullScreen(nil)
    }

    func windowDidEnterFullScreen(_ notification: Notification) { rebuildStatusMenu(); syncTitlebar() }
    func windowDidExitFullScreen(_ notification: Notification) { placeWindow(); syncTitlebar() }
    func windowDidResize(_ notification: Notification) { layoutTrafficLights() }
    func windowDidBecomeKey(_ notification: Notification) { layoutTrafficLights() }
    func windowDidResignKey(_ notification: Notification) { layoutTrafficLights() }

    // MARK: - 红黄绿 + 顶栏拖动

    /// 把红黄绿竖直放到页面顶栏（64px）正中，左边距 20，保持系统原来的间距。AppKit 会时不时把它们放回原位，所以多处重复调用。
    private func layoutTrafficLights() {
        guard !window.styleMask.contains(.fullScreen),
              let close = window.standardWindowButton(.closeButton),
              let mini = window.standardWindowButton(.miniaturizeButton),
              let zoom = window.standardWindowButton(.zoomButton),
              let bar = close.superview else { return }
        // 按钮所在的标题栏容器只有 ~28pt 高，按钮要放到 32pt 处就得把容器加高，否则会被裁掉
        if let container = bar.superview, container.frame.height < lightsCenterY * 2 {
            let h = lightsCenterY * 2
            container.frame = NSRect(x: container.frame.minX, y: window.frame.height - h, width: container.frame.width, height: h)
        }
        let spacing = mini.frame.minX - close.frame.minX
        // 目标中心点：窗口坐标（左下角原点）里离顶部 lightsCenterY；换算到按钮父视图的坐标里
        let center = bar.convert(NSPoint(x: 0, y: window.frame.height - lightsCenterY), from: nil)
        for (i, b) in [close, mini, zoom].enumerated() {
            b.setFrameOrigin(NSPoint(x: lightsLeft + CGFloat(i) * spacing, y: (center.y - b.frame.height / 2).rounded()))
        }
    }

    /// 告诉页面：现在是窗口（给红黄绿留位置）还是全屏（不用留）
    private func syncTitlebar() {
        let on = !window.styleMask.contains(.fullScreen)
        webView.evaluateJavaScript("window.__deckTitlebar && window.__deckTitlebar(\(on))", completionHandler: nil)
        if !on { webView.dragHeight = 0 }
        layoutTrafficLights()
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        // 只认监控台主框架（网页卡片的 iframe 不能改窗口拖动区域）
        let origin = message.frameInfo.securityOrigin
        guard message.frameInfo.isMainFrame, origin.host == "127.0.0.1" else { return }
        guard message.name == "deckTitlebar", let text = message.body as? String,
              let data = text.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        let height = (obj["height"] as? NSNumber)?.doubleValue ?? 0
        let holes = (obj["holes"] as? [[NSNumber]] ?? []).compactMap { a -> CGRect? in
            a.count == 4 ? CGRect(x: a[0].doubleValue, y: a[1].doubleValue, width: a[2].doubleValue, height: a[3].doubleValue) : nil
        }
        webView.dragHeight = window.styleMask.contains(.fullScreen) ? 0 : CGFloat(height)
        webView.holes = holes
    }

    @objc private func toggleLogin() {
        do {
            if SMAppService.mainApp.status == .enabled { try SMAppService.mainApp.unregister() }
            else { try SMAppService.mainApp.register() }
        } catch {
            let alert = NSAlert()
            alert.messageText = "没能修改开机自启"
            alert.informativeText = "请到 系统设置 → 通用 → 登录项 里手动添加 Deck。（\(error.localizedDescription)）"
            alert.runModal()
        }
        rebuildStatusMenu()
    }

    // MARK: - 网页

    /// 监控台以外的链接（GitHub、Mac 管家首页）交给默认浏览器打开，监控台窗口始终停在 /dash。
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { return decisionHandler(.cancel) }
        let isDash = url.host == "127.0.0.1" && url.path.hasPrefix("/dash")
        if isDash || url.absoluteString == "about:blank" || navigationAction.navigationType == .reload || navigationAction.targetFrame?.isMainFrame == false {
            return decisionHandler(.allow)
        }
        if ["http", "https"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard webView === self.webView, webView.url?.absoluteString != "about:blank" else { return }  // 对话玻璃里的网页不管错误层和标题栏
        errorView.hide(); syncTitlebar()
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        guard webView === self.webView else { return }
        errorView.show(title: "连不上 Mac 管家", detail: "服务可能刚被关掉了。15 秒后自动重试，也可以现在点重试。",
                       showRetry: true, in: window.contentView!)
        scheduleRetry()
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        if webView === self.webView && useNotch { return }  // 刘海模式下主窗口本来就是空的
        webView.reload()
    }
}

/// Deck 主窗口：一直用「激活」外观。
/// Deck 常年放在 iPad 扩展屏上，键盘焦点基本都在主屏的别的应用里；系统会把非激活窗口里的液态玻璃
/// 画成扁平的磨砂样子（NSGlassEffectView 没有公开开关，NSVisualEffectView 的 state = .active 对它无效），
/// 所以这里覆盖 AppKit 判断窗口外观的两个方法，让玻璃和红黄绿按钮始终按激活状态画。
/// 只影响外观，不改变键盘焦点和窗口层级。这是私有方法：将来系统改了名字，只会回到「没点中时变磨砂」，不会出错。
/// 关掉：defaults write io.github.deckformac.deck glassAlwaysActive -bool NO
final class DeckWindow: NSWindow {
    private var alwaysActive: Bool { UserDefaults.standard.object(forKey: "glassAlwaysActive") as? Bool ?? true }
    @objc(_hasActiveAppearance) func deckHasActiveAppearance() -> Bool { alwaysActive || isKeyWindow }
    @objc(_hasActiveAppearanceIgnoringKeyFocus) func deckHasActiveAppearanceIgnoringKeyFocus() -> Bool { alwaysActive || isMainWindow }
}

/// 网页视图：页面顶栏里不是按钮的空白处，按住拖动窗口、双击按系统设置缩放/最小化；按钮照常交给网页。
final class DeckWebView: WKWebView {
    var dragHeight: CGFloat = 0   // 页面顶栏底边（CSS px，从网页左上角算）
    var holes: [CGRect] = []      // 顶栏里的按钮（CSS px）

    override func mouseDown(with event: NSEvent) {
        if dragHeight > 0, let window = window {
            let p = convert(event.locationInWindow, from: nil)
            let topY = isFlipped ? p.y : bounds.height - p.y
            let scale = max(magnification, 0.01)
            let css = CGPoint(x: p.x / scale, y: topY / scale)
            if css.y < dragHeight && !holes.contains(where: { $0.contains(css) }) {
                if event.clickCount == 2 {
                    let action = UserDefaults.standard.string(forKey: "AppleActionOnDoubleClick") ?? "Maximize"
                    if action == "Minimize" { window.miniaturize(nil) } else if action != "None" { window.zoom(nil) }
                } else {
                    window.performDrag(with: event)
                }
                return
            }
        }
        super.mouseDown(with: event)
    }
}

/// WKUserContentController 会强引用 handler，用这个中转避免 AppDelegate 循环引用
final class WeakScriptHandler: NSObject, WKScriptMessageHandler {
    weak var target: WKScriptMessageHandler?
    init(_ target: WKScriptMessageHandler) { self.target = target }
    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) { target?.userContentController(c, didReceive: m) }
}
