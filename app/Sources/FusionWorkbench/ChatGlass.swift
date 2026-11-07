import AppKit
import WebKit

/// 助手对话面板：系统液态玻璃（NSGlassEffectView，macOS 26+）里放一个透明的对话网页。
///
/// - 主页面（/dash?app=1）通过 `deckGlass` 报面板的位置、开关、染色（CSS px），这里换算后摆好玻璃；
///   `{relay: {...}}` 形式的消息原样转给对话页。
/// - 对话页（/dash?app=1&pane=chat）通过 `deckChat` 发 `{relay: {...}}`，这里转给主页面。
/// - 两个网页都调用页面里的 `window.__deckRelay(msg)` 收话。
/// macOS 26 以下不创建玻璃、不注册 `deckGlass`，主页面就用原来的网页面板。
@available(macOS 26.0, *)
final class ChatGlass: NSObject, WKScriptMessageHandler {
    let glass = NSGlassEffectView()
    let web: WKWebView
    private weak var main: WKWebView?
    private var isOpen = false

    init(main: WKWebView, delegate: (WKNavigationDelegate & WKUIDelegate)) {
        self.main = main
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()  // 和主页面共用存储：主题、布局设置一致
        web = WKWebView(frame: .zero, configuration: config)
        super.init()
        config.userContentController.add(WeakScriptHandler(self), name: "deckChat")
        main.configuration.userContentController.add(WeakScriptHandler(self), name: "deckGlass")
        web.setValue(false, forKey: "drawsBackground")  // 透明：玻璃从网页背后透出来
        web.underPageBackgroundColor = .clear
        web.navigationDelegate = delegate
        web.uiDelegate = delegate
        web.autoresizingMask = [.width, .height]
        glass.style = .clear  // 清透款（和桌面整理软件的默认 A 一样）；透明度由染色控制
        glass.cornerRadius = 28
        glass.contentView = web
        glass.isHidden = true
        glass.alphaValue = 0
    }

    func attach(above view: NSView) {
        view.superview?.addSubview(glass, positioned: .above, relativeTo: view)
    }

    func load(dash url: URL) {
        guard var c = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return }
        c.queryItems = (c.queryItems ?? []).filter { $0.name != "pane" } + [URLQueryItem(name: "pane", value: "chat")]
        c.fragment = nil
        if let u = c.url { web.load(URLRequest(url: u)) }
    }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        // 只认监控台自己的主框架：网页卡片（iframe 里的第三方网站）发来的消息一律不理，免得它们冒充你给助手下指令
        guard Self.trusted(message) else { return }
        guard let text = message.body as? String, let data = text.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        if let relay = obj["relay"] {
            deliver(relay, to: message.name == "deckChat" ? main : web)
            return
        }
        guard message.name == "deckGlass" else { return }
        place(obj)
    }

    /// 刘海模式时卸掉对话页（同一时间只留一个页面连语音服务）
    func unload() {
        hide()
        web.load(URLRequest(url: URL(string: "about:blank")!))
    }

    static func trusted(_ message: WKScriptMessage) -> Bool {
        let o = message.frameInfo.securityOrigin
        return message.frameInfo.isMainFrame && o.protocol == "http" && o.host == "127.0.0.1"
    }

    private func deliver(_ msg: Any, to target: WKWebView?) {
        guard let target, JSONSerialization.isValidJSONObject(msg),
              let data = try? JSONSerialization.data(withJSONObject: msg),
              let json = String(data: data, encoding: .utf8) else { return }
        target.evaluateJavaScript("window.__deckRelay && window.__deckRelay(\(json))", completionHandler: nil)
    }

    /// {open, x, y, w, h, r, tint, dark}：CSS px，左上角为原点
    private func place(_ o: [String: Any]) {
        guard let main, let host = glass.superview else { return }
        func num(_ k: String) -> CGFloat { CGFloat((o[k] as? NSNumber)?.doubleValue ?? 0) }
        let s = max(main.magnification, 0.01)
        let mf = main.frame
        let frame = NSRect(x: mf.minX + num("x") * s,
                           y: (host.isFlipped ? mf.minY + num("y") * s : mf.maxY - (num("y") + num("h")) * s),
                           width: num("w") * s, height: num("h") * s).integral
        let dark = (o["dark"] as? Bool) ?? false
        let tint = min(max(num("tint"), 0), 1)
        glass.cornerRadius = (num("r") > 0 ? num("r") : 28) * s
        glass.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        web.appearance = glass.appearance
        // 浅色主题用和白天界面搭的雾蓝灰（白玻璃太亮）；深色主题用深蓝灰
        glass.tintColor = dark ? NSColor(srgbRed: 0.11, green: 0.125, blue: 0.18, alpha: tint)
                               : NSColor(srgbRed: 0.43, green: 0.50, blue: 0.62, alpha: tint)
        let open = (o["open"] as? Bool) ?? false && frame.width > 40 && frame.height > 40
        open ? show(frame) : hide()
    }

    private func show(_ frame: NSRect) {
        let appearing = !isOpen
        isOpen = true
        if appearing {
            // 从顶栏「助手」胶囊的方向落下来：先放在上方一点、透明，再弹到位
            glass.frame = frame.offsetBy(dx: 0, dy: glass.superview?.isFlipped == true ? -14 : 14)
            glass.isHidden = false
        }
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = appearing ? 0.34 : 0.18
            ctx.timingFunction = CAMediaTimingFunction(controlPoints: 0.2, 0.9, 0.25, 1)
            ctx.allowsImplicitAnimation = true
            glass.animator().frame = frame
            glass.animator().alphaValue = 1
        }
        if appearing { glass.window?.makeFirstResponder(web) }
    }

    private func hide() {
        guard isOpen else { return }
        isOpen = false
        let end = glass.frame.offsetBy(dx: 0, dy: glass.superview?.isFlipped == true ? -10 : 10)
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.2
            ctx.allowsImplicitAnimation = true
            glass.animator().frame = end
            glass.animator().alphaValue = 0
        }, completionHandler: { [weak self] in
            guard let self, !self.isOpen else { return }
            self.glass.isHidden = true
        })
        if let main { glass.window?.makeFirstResponder(main) }
    }
}
