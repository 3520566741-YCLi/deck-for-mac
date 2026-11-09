// DeckHands：Vesper 的「手」。只做四件事：读出当前应用里能点的元素、点一个、打字、按快捷键。
// 另有键盘清洁模式（--clean，见 CleanMode.swift），是单独的进程。
// 怎么点由 Vesper（DeepSeek + Jev）在 Deck 服务里决定；这里只执行，并且自己守住几条底线：
// - 菜单栏开关「允许 Vesper 操作电脑」关着时，什么都不做。
// - 急停：Deck 的停止（说「停」/ ⌃⌥⌘. / → 键 / 页面停止按钮，经 Deck 发 stop）、菜单「立即停止」、连按 3 次 Esc。
//   停下后拒绝一切动作，直到下一次新的提问（Deck 发 arm）。
// - 永远不往密码框里打字，也不读密码框的内容。
// - 只听本机 Unix socket（~/Library/Application Support/Deck/hands.sock，权限 600，且核对对方是同一个用户）。
// 需要「辅助功能」权限（系统设置 → 隐私与安全性 → 辅助功能 → DeckHands）。
import AppKit
import ApplicationServices

// MARK: - 状态

final class State {
    static let shared = State()
    let lock = NSLock()
    var stopped = false
    var acting = false
    var enabled: Bool {
        get { UserDefaults.standard.object(forKey: "enabled") as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: "enabled") }
    }
    func isStopped() -> Bool { lock.lock(); defer { lock.unlock() }; return stopped }
    func setStopped(_ v: Bool) { lock.lock(); stopped = v; lock.unlock() }
}

let supportDir: URL = {
    let u = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Deck")
    try? FileManager.default.createDirectory(at: u, withIntermediateDirectories: true)
    return u
}()
let sockPath = supportDir.appendingPathComponent("hands.sock").path
let logURL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/DeckHands.log")

func log(_ s: String) {
    let line = "\(ISO8601DateFormatter().string(from: Date())) \(s)\n"
    guard let d = line.data(using: .utf8) else { return }
    if let h = try? FileHandle(forWritingTo: logURL) {
        h.seekToEndOfFile(); h.write(d); try? h.close()
    } else {
        try? d.write(to: logURL)
    }
}

// MARK: - 辅助功能小工具

func attr(_ e: AXUIElement, _ name: String) -> CFTypeRef? {
    var v: CFTypeRef?
    return AXUIElementCopyAttributeValue(e, name as CFString, &v) == .success ? v : nil
}
func sattr(_ e: AXUIElement, _ name: String) -> String? {
    guard let s = attr(e, name) as? String else { return nil }
    let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
    return t.isEmpty ? nil : t
}
func kids(_ e: AXUIElement) -> [AXUIElement] { (attr(e, kAXChildrenAttribute) as? [AXUIElement]) ?? [] }
func actionNames(_ e: AXUIElement) -> [String] {
    var a: CFArray?
    return AXUIElementCopyActionNames(e, &a) == .success ? ((a as? [String]) ?? []) : []
}
func frameOf(_ e: AXUIElement) -> CGRect? {
    guard let p = attr(e, kAXPositionAttribute), let s = attr(e, kAXSizeAttribute),
          CFGetTypeID(p) == AXValueGetTypeID(), CFGetTypeID(s) == AXValueGetTypeID() else { return nil }
    var pt = CGPoint.zero, sz = CGSize.zero
    AXValueGetValue(p as! AXValue, .cgPoint, &pt)
    AXValueGetValue(s as! AXValue, .cgSize, &sz)
    return CGRect(origin: pt, size: sz)
}
func isSecure(_ e: AXUIElement) -> Bool {
    (sattr(e, kAXSubroleAttribute) ?? "") == (kAXSecureTextFieldSubrole as String)
        || (sattr(e, kAXRoleAttribute) ?? "") == "AXSecureTextField"
}
func clip(_ s: String, _ n: Int) -> String {
    let one = s.replacingOccurrences(of: "\n", with: " ")
    return one.count <= n ? one : String(one.prefix(n)) + "…"
}

let ACTIONABLE: Set<String> = ["AXButton", "AXLink", "AXMenuItem", "AXMenuBarItem", "AXCheckBox", "AXRadioButton",
                               "AXPopUpButton", "AXMenuButton", "AXComboBox", "AXTextField", "AXTextArea", "AXSearchField",
                               "AXTab", "AXDisclosureTriangle", "AXSlider", "AXIncrementor", "AXCell", "AXRow", "AXDockItem"]
let TEXT_ROLES: Set<String> = ["AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"]

/// 元素没有自己的名字时，从子元素里找一段文字当名字（按钮里套文字很常见）。
func innerText(_ e: AXUIElement, depth: Int = 0) -> String? {
    if depth > 3 { return nil }
    for c in kids(e).prefix(8) {
        let role = sattr(c, kAXRoleAttribute) ?? ""
        if role == "AXStaticText", let v = sattr(c, kAXValueAttribute) ?? sattr(c, kAXTitleAttribute) { return v }
        if role == "AXImage", let d = sattr(c, kAXDescriptionAttribute) { return d }
        if let t = innerText(c, depth: depth + 1) { return t }
    }
    return nil
}

struct Snap {
    let id: String
    let pid: pid_t
    let at: Date
    var elems: [AXUIElement]
    var frames: [CGRect?]
}
var snaps: [String: Snap] = [:]
var snapOrder: [String] = []

func onScreen(_ r: CGRect) -> Bool {
    // AX 坐标原点在主屏左上角；NSScreen 原点在主屏左下角。换算后看是否落在任何一块屏幕上。
    guard let mainH = NSScreen.screens.first?.frame.height else { return true }
    let flipped = CGRect(x: r.minX, y: mainH - r.maxY, width: r.width, height: r.height)
    return NSScreen.screens.contains { $0.frame.intersects(flipped) }
}

func elements(pid: pid_t, max: Int) -> [String: Any] {
    let app = AXUIElementCreateApplication(pid)
    AXUIElementSetMessagingTimeout(app, 0.6)
    var roots: [AXUIElement] = []
    if let w = attr(app, kAXFocusedWindowAttribute) { roots.append(w as! AXUIElement) }
    for w in ((attr(app, kAXWindowsAttribute) as? [AXUIElement]) ?? []).prefix(3) where !roots.contains(where: { CFEqual($0, w) }) {
        roots.append(w)
    }
    if let mb = attr(app, kAXMenuBarAttribute) { roots.append(mb as! AXUIElement) }
    var out: [[String: Any]] = []
    var refs: [AXUIElement] = []
    var frames: [CGRect?] = []
    var visited = 0
    let deadline = Date().addingTimeInterval(2.0)
    func walk(_ e: AXUIElement, _ depth: Int, _ inMenuBar: Bool) {
        if visited > 4000 || out.count >= max || depth > 40 || Date() > deadline { return }
        visited += 1
        let role = sattr(e, kAXRoleAttribute) ?? ""
        let acts = actionNames(e)
        let press = acts.contains(kAXPressAction as String)
        if ACTIONABLE.contains(role) || press {
            let fr = frameOf(e)
            let visible = inMenuBar || (fr.map { $0.width > 1 && $0.height > 1 && onScreen($0) } ?? false)
            let secure = isSecure(e)
            var label = sattr(e, kAXTitleAttribute) ?? sattr(e, kAXDescriptionAttribute) ?? ""
            if label.isEmpty, !secure, !TEXT_ROLES.contains(role), let v = attr(e, kAXValueAttribute) as? String { label = v }
            if label.isEmpty { label = innerText(e) ?? "" }
            if label.isEmpty { label = sattr(e, kAXPlaceholderValueAttribute as String) ?? "" }
            if label.isEmpty { label = sattr(e, kAXHelpAttribute) ?? "" }
            let enabled = (attr(e, kAXEnabledAttribute) as? Bool) ?? true
            if visible && !(label.isEmpty && !TEXT_ROLES.contains(role)) {
                var d: [String: Any] = ["i": out.count, "role": role, "label": clip(label, 80), "enabled": enabled]
                if let rd = sattr(e, kAXRoleDescriptionAttribute) { d["kind"] = clip(rd, 20) }
                if let h = sattr(e, kAXHelpAttribute), h != label { d["help"] = clip(h, 60) }
                if TEXT_ROLES.contains(role) {
                    d["text"] = true
                    if let ph = sattr(e, kAXPlaceholderValueAttribute as String) { d["placeholder"] = clip(ph, 40) }
                }
                if secure { d["secure"] = true }
                if inMenuBar { d["menu"] = true }
                if let f = fr { d["frame"] = [Int(f.minX), Int(f.minY), Int(f.width), Int(f.height)] }
                out.append(d); refs.append(e); frames.append(fr)
            }
            // 菜单栏只要第一层（「文件」「编辑」…），不展开没打开的菜单
            if role == "AXMenuBarItem" { return }
        }
        for c in kids(e) { walk(c, depth + 1, inMenuBar || role == "AXMenuBar") }
    }
    for r in roots { walk(r, 0, false) }
    let id = String(UUID().uuidString.prefix(8)).lowercased()
    snaps[id] = Snap(id: id, pid: pid, at: Date(), elems: refs, frames: frames)
    snapOrder.append(id)
    while snapOrder.count > 4 { snaps.removeValue(forKey: snapOrder.removeFirst()) }
    let ra = NSRunningApplication(processIdentifier: pid)
    return ["ok": true, "snap": id, "app": ra?.localizedName ?? "", "bundle": ra?.bundleIdentifier ?? "",
            "elements": out, "truncated": out.count >= max || visited > 4000]
}

// MARK: - 动作

func guardAct() -> String? {
    if !AXIsProcessTrusted() { return "DeckHands 还没有辅助功能权限" }
    if !State.shared.enabled { return "菜单栏里「允许 Vesper 操作电脑」关着" }
    if State.shared.isStopped() { return "刚被急停了，这一轮不再动" }
    return nil
}

func lookup(_ req: [String: Any]) -> (Snap, Int, AXUIElement)? {
    guard let sid = req["snap"] as? String, let snap = snaps[sid], let i = req["i"] as? Int,
          i >= 0, i < snap.elems.count else { return nil }
    if Date().timeIntervalSince(snap.at) > 180 { return nil }
    return (snap, i, snap.elems[i])
}

/// 元素是否还在原处（页面变了就不点，免得点错）。
func stillThere(_ snap: Snap, _ i: Int, _ e: AXUIElement) -> Bool {
    guard sattr(e, kAXRoleAttribute) != nil else { return false }
    guard let old = snap.frames[i] else { return true }
    guard let now = frameOf(e) else { return false }
    return abs(now.minX - old.minX) < 8 && abs(now.minY - old.minY) < 8
}

var overlay: NSWindow?
/// 点之前在目标上闪一下红框，让人看得见 Vesper 要点哪里（0.35 秒）。
func flash(_ r: CGRect?) {
    guard let r = r, let mainH = NSScreen.screens.first?.frame.height else { return }
    DispatchQueue.main.sync {
        let f = CGRect(x: r.minX - 3, y: mainH - r.maxY - 3, width: r.width + 6, height: r.height + 6)
        let w = overlay ?? {
            let w = NSWindow(contentRect: f, styleMask: .borderless, backing: .buffered, defer: false)
            w.isOpaque = false; w.backgroundColor = .clear; w.level = .screenSaver; w.ignoresMouseEvents = true
            w.collectionBehavior = [.canJoinAllSpaces, .transient]
            let v = NSView(); v.wantsLayer = true
            v.layer?.borderColor = NSColor.systemRed.cgColor; v.layer?.borderWidth = 3; v.layer?.cornerRadius = 6
            w.contentView = v
            overlay = w
            return w
        }()
        w.setFrame(f, display: true)
        w.orderFrontRegardless()
    }
    Thread.sleep(forTimeInterval: 0.35)
    DispatchQueue.main.async { overlay?.orderOut(nil) }
}

func clickAt(_ p: CGPoint) {
    let src = CGEventSource(stateID: .hidSystemState)
    for t in [CGEventType.leftMouseDown, .leftMouseUp] {
        CGEvent(mouseEventSource: src, mouseType: t, mouseCursorPosition: p, mouseButton: .left)?.post(tap: .cghidEventTap)
        Thread.sleep(forTimeInterval: 0.04)
    }
}

func activate(_ pid: pid_t) {
    if let ra = NSRunningApplication(processIdentifier: pid), !ra.isActive {
        ra.activate()
        Thread.sleep(forTimeInterval: 0.25)
    }
}

func press(_ req: [String: Any]) -> [String: Any] {
    if let why = guardAct() { return ["ok": false, "error": why] }
    guard let (snap, i, e) = lookup(req) else { return ["ok": false, "error": "这批屏幕元素已经过期，先重新看一下屏幕"] }
    guard stillThere(snap, i, e) else { return ["ok": false, "error": "屏幕变了，这个元素不在原来的位置，先重新看一下"] }
    if (attr(e, kAXEnabledAttribute) as? Bool) == false { return ["ok": false, "error": "这个按钮现在是灰的，点不了"] }
    flash(snap.frames[i])
    if State.shared.isStopped() { return ["ok": false, "error": "刚被急停了"] }
    let role = sattr(e, kAXRoleAttribute) ?? ""
    var method = "press"
    if TEXT_ROLES.contains(role) {
        if AXUIElementSetAttributeValue(e, kAXFocusedAttribute as CFString, kCFBooleanTrue) != .success, let f = frameOf(e) {
            activate(snap.pid); clickAt(CGPoint(x: f.midX, y: f.midY)); method = "click"
        } else { method = "focus" }
    } else if AXUIElementPerformAction(e, kAXPressAction as CFString) != .success {
        guard let f = frameOf(e) else { return ["ok": false, "error": "点不了这个元素"] }
        activate(snap.pid); clickAt(CGPoint(x: f.midX, y: f.midY)); method = "click"
    }
    log("press app=\(NSRunningApplication(processIdentifier: snap.pid)?.bundleIdentifier ?? "?") role=\(role) label=\(clip(sattr(e, kAXTitleAttribute) ?? sattr(e, kAXDescriptionAttribute) ?? "", 40)) via=\(method)")
    return ["ok": true, "method": method]
}

func focusedElement() -> AXUIElement? {
    let sys = AXUIElementCreateSystemWide()
    guard let f = attr(sys, kAXFocusedUIElementAttribute) else { return nil }
    return (f as! AXUIElement)
}

func typeText(_ req: [String: Any]) -> [String: Any] {
    if let why = guardAct() { return ["ok": false, "error": why] }
    guard let text = req["text"] as? String, !text.isEmpty else { return ["ok": false, "error": "没有要输入的内容"] }
    if text.count > 2000 { return ["ok": false, "error": "一次最多输入 2000 个字"] }
    if req["snap"] != nil {
        guard let (snap, i, e) = lookup(req) else { return ["ok": false, "error": "这批屏幕元素已经过期，先重新看一下屏幕"] }
        guard stillThere(snap, i, e) else { return ["ok": false, "error": "屏幕变了，先重新看一下"] }
        if isSecure(e) { return ["ok": false, "error": "这是密码框，Vesper 不往密码框里输入"] }
        flash(snap.frames[i])
        activate(snap.pid)
        if AXUIElementSetAttributeValue(e, kAXFocusedAttribute as CFString, kCFBooleanTrue) != .success, let f = frameOf(e) {
            clickAt(CGPoint(x: f.midX, y: f.midY))
        }
        Thread.sleep(forTimeInterval: 0.12)
    }
    if let f = focusedElement(), isSecure(f) { return ["ok": false, "error": "现在焦点在密码框里，Vesper 不往密码框里输入"] }
    let src = CGEventSource(stateID: .hidSystemState)
    let units = Array(text.utf16)
    var k = 0
    while k < units.count {
        if State.shared.isStopped() { return ["ok": false, "error": "输入到一半被急停了", "typed": k] }
        var n = min(16, units.count - k)
        // 不把一个代理对（emoji 等）切成两半
        if n < units.count - k, UTF16.isLeadSurrogate(units[k + n - 1]) { n -= 1 }
        var chunk = Array(units[k..<(k + n)])
        for down in [true, false] {
            let ev = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: down)
            ev?.flags = []
            ev?.keyboardSetUnicodeString(stringLength: chunk.count, unicodeString: &chunk)
            ev?.post(tap: .cghidEventTap)
        }
        k += n
        Thread.sleep(forTimeInterval: 0.02)
    }
    log("type app=\(NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? "?") chars=\(text.count)")
    return ["ok": true, "chars": text.count]
}

let KEYS: [String: CGKeyCode] = [
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14,
    "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27,
    "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "return": 36, "enter": 36, "l": 37, "j": 38,
    "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "tab": 48, "space": 49, "`": 50,
    "delete": 51, "backspace": 51, "esc": 53, "escape": 53, "left": 123, "right": 124, "down": 125, "up": 126,
    "forwarddelete": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109,
    "f11": 103, "f12": 111]

func pressKey(_ req: [String: Any]) -> [String: Any] {
    if let why = guardAct() { return ["ok": false, "error": why] }
    guard let combo = (req["keys"] as? String)?.lowercased().replacingOccurrences(of: " ", with: ""), !combo.isEmpty else {
        return ["ok": false, "error": "没有按键"]
    }
    var flags: CGEventFlags = []
    var code: CGKeyCode?
    for part in combo.split(separator: "+").map(String.init) {
        switch part {
        case "cmd", "command", "⌘": flags.insert(.maskCommand)
        case "shift", "⇧": flags.insert(.maskShift)
        case "opt", "option", "alt", "⌥": flags.insert(.maskAlternate)
        case "ctrl", "control", "⌃": flags.insert(.maskControl)
        default:
            guard code == nil, let c = KEYS[part] else { return ["ok": false, "error": "不认识的按键：\(part)"] }
            code = c
        }
    }
    guard let kc = code else { return ["ok": false, "error": "只有修饰键，没有主键"] }
    let src = CGEventSource(stateID: .hidSystemState)
    for down in [true, false] {
        let ev = CGEvent(keyboardEventSource: src, virtualKey: kc, keyDown: down)
        ev?.flags = flags
        ev?.post(tap: .cghidEventTap)
        Thread.sleep(forTimeInterval: 0.03)
    }
    log("key app=\(NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? "?") keys=\(combo)")
    return ["ok": true]
}

func scroll(_ req: [String: Any]) -> [String: Any] {
    if let why = guardAct() { return ["ok": false, "error": why] }
    let dir = (req["dir"] as? String) ?? "down"
    let n = Int32(max(1, min(15, (req["amount"] as? Int) ?? 5)))
    let dy: Int32 = dir == "up" ? n : dir == "down" ? -n : 0
    let dx: Int32 = dir == "left" ? n : dir == "right" ? -n : 0
    let ev = CGEvent(scrollWheelEvent2Source: nil, units: .line, wheelCount: 2, wheel1: dy, wheel2: dx, wheel3: 0)
    ev?.post(tap: .cghidEventTap)
    return ["ok": true]
}

func status() -> [String: Any] {
    let fa = NSWorkspace.shared.frontmostApplication
    return ["ok": true, "trusted": AXIsProcessTrusted(), "enabled": State.shared.enabled, "stopped": State.shared.isStopped(),
            "front": ["name": fa?.localizedName ?? "", "bundle": fa?.bundleIdentifier ?? "", "pid": Int(fa?.processIdentifier ?? 0)]]
}

func handle(_ req: [String: Any]) -> [String: Any] {
    switch req["cmd"] as? String ?? "" {
    case "status": return status()
    case "arm":
        State.shared.setStopped(false); DispatchQueue.main.async { refreshMenu() }; return status()
    case "stop":
        stopNow("deck"); return status()
    case "elements":
        if !AXIsProcessTrusted() { return ["ok": false, "error": "DeckHands 还没有辅助功能权限"] }
        var pid = (req["pid"] as? Int).map { pid_t($0) } ?? NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
        if let b = req["bundle"] as? String, let ra = NSRunningApplication.runningApplications(withBundleIdentifier: b).first {
            pid = ra.processIdentifier
        }
        // 按应用名找（Vesper 说「在文本编辑里点…」时）：不依赖它是不是前台，他正在用别的窗口也不会看错
        if let n = (req["name"] as? String)?.lowercased(), !n.isEmpty {
            let hit = NSWorkspace.shared.runningApplications.first { ra in
                ra.activationPolicy == .regular &&
                ((ra.localizedName ?? "").lowercased() == n || ra.bundleURL?.deletingPathExtension().lastPathComponent.lowercased() == n)
            }
            guard let ra = hit else { return ["ok": false, "error": "「\(n)」没在运行"] }
            pid = ra.processIdentifier
        }
        if pid == 0 { return ["ok": false, "error": "找不到前台应用"] }
        return elements(pid: pid, max: max(10, min(400, (req["max"] as? Int) ?? 250)))
    case "press": return press(req)
    case "type": return typeText(req)
    case "key": return pressKey(req)
    case "scroll": return scroll(req)
    default: return ["ok": false, "error": "未知命令"]
    }
}

// MARK: - 本机 socket

let work = DispatchQueue(label: "hands.work")

func serve() {
    unlink(sockPath)
    let fd = socket(AF_UNIX, SOCK_STREAM, 0)
    guard fd >= 0 else { log("socket failed"); return }
    var addr = sockaddr_un()
    addr.sun_family = sa_family_t(AF_UNIX)
    let bytes = Array(sockPath.utf8)
    guard bytes.count < MemoryLayout.size(ofValue: addr.sun_path) else { log("socket path too long"); return }
    withUnsafeMutableBytes(of: &addr.sun_path) { p in
        for (k, b) in bytes.enumerated() { p[k] = b }
        p[bytes.count] = 0
    }
    let old = umask(0o077)
    let ok = withUnsafePointer(to: &addr) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
    }
    umask(old)
    guard ok == 0 else { log("bind failed \(errno)"); return }
    chmod(sockPath, 0o600)
    listen(fd, 8)
    log("listening")
    // 每个连接各开一个线程处理：正在打字/点击时，stop 还能马上进来
    let conns = DispatchQueue(label: "hands.conns", attributes: .concurrent)
    Thread.detachNewThread {
        while true {
            let c = accept(fd, nil, nil)
            if c < 0 { continue }
            conns.async { serveOne(c) }
        }
    }
}

func serveOne(_ c: Int32) {
    var uid: uid_t = 0, gid: gid_t = 0
    if getpeereid(c, &uid, &gid) != 0 || uid != getuid() { close(c); return }
    var tv = timeval(tv_sec: 5, tv_usec: 0)
    setsockopt(c, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
    var data = Data()
    var buf = [UInt8](repeating: 0, count: 8192)
    while data.count < 65536 {
        let n = read(c, &buf, buf.count)
        if n <= 0 { break }
        data.append(buf, count: n)
        if buf[n - 1] == 10 { break }
    }
    let req = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
    // stop / status 不排队：急停要立刻生效，不能等前一个动作做完
    let cmd = req["cmd"] as? String ?? ""
    let res: [String: Any]
    if cmd == "stop" || cmd == "status" { res = handle(req) } else { res = work.sync { handle(req) } }
    var out = (try? JSONSerialization.data(withJSONObject: res)) ?? Data("{}".utf8)
    out.append(10)
    out.withUnsafeBytes { _ = write(c, $0.baseAddress, out.count) }
    close(c)
}

// MARK: - 菜单栏

var statusItem: NSStatusItem!
let menu = NSMenu()

func stopNow(_ why: String) {
    State.shared.setStopped(true)
    log("STOP (\(why))")
    DispatchQueue.main.async { refreshMenu() }
    // 自己这边发起的急停（菜单 / Esc×3）也走 Deck 唯一的打断路径：语音服务 hub.stop() → 页面停掉这一轮
    if why != "deck" {
        var r = URLRequest(url: URL(string: "http://127.0.0.1:8778/stop")!, timeoutInterval: 2)
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = Data(#"{"source":"hands"}"#.utf8)
        URLSession.shared.dataTask(with: r).resume()
    }
}

func refreshMenu() {
    let st = State.shared
    statusItem.button?.title = !st.enabled ? "✋̸" : st.isStopped() ? "⏹" : "✋"
    statusItem.button?.toolTip = "DeckHands：Vesper 操作电脑的开关"
    menu.removeAllItems()
    let trusted = AXIsProcessTrusted()
    let head = NSMenuItem(title: trusted ? "辅助功能权限：已开" : "辅助功能权限：未开（点这里去开）", action: trusted ? nil : #selector(Actions.openPrefs), keyEquivalent: "")
    head.target = Actions.shared
    menu.addItem(head)
    menu.addItem(.separator())
    let en = NSMenuItem(title: "允许 Vesper 操作电脑", action: #selector(Actions.toggle), keyEquivalent: "")
    en.state = st.enabled ? .on : .off
    en.target = Actions.shared
    menu.addItem(en)
    let stop = NSMenuItem(title: st.isStopped() ? "已急停（下次提问自动恢复）" : "立即停止（或连按 3 次 Esc）", action: #selector(Actions.stop), keyEquivalent: "")
    stop.target = Actions.shared
    menu.addItem(stop)
    menu.addItem(.separator())
    let log = NSMenuItem(title: "查看操作记录", action: #selector(Actions.showLog), keyEquivalent: "")
    log.target = Actions.shared
    menu.addItem(log)
    let q = NSMenuItem(title: "退出 DeckHands", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
    menu.addItem(q)
}

final class Actions: NSObject, NSMenuDelegate {
    static let shared = Actions()
    @objc func toggle() { State.shared.enabled.toggle(); log("enabled=\(State.shared.enabled)"); refreshMenu() }
    @objc func stop() { stopNow("menu") }
    @objc func openPrefs() {
        let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(opts)
        NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!)
    }
    @objc func showLog() { NSWorkspace.shared.open(logURL) }
    func menuWillOpen(_ menu: NSMenu) { refreshMenu() }
}

// MARK: - 启动

// 键盘清洁模式（CleanMode.swift）：Deck 用 `open -n` 另起一个进程，和这个菜单栏实例互不影响
if CommandLine.arguments.contains("--clean") { runCleanMode() }

signal(SIGPIPE, SIG_IGN)  // 对方先断开时 write 不能把整个程序带走
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
statusItem.menu = menu
menu.delegate = Actions.shared
refreshMenu()
if !AXIsProcessTrusted() { Actions.shared.openPrefs() }

// 备用急停：连按三次 Esc（只是旁听按键，不注册快捷键；⌃⌥⌘. 归 DeckVoice，经 Deck 的 stop 传到这里）
var escTimes: [Date] = []
NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { e in
    if e.keyCode == 53 {
        let now = Date()
        escTimes = escTimes.filter { now.timeIntervalSince($0) < 1.2 } + [now]
        if escTimes.count >= 3 { escTimes = []; stopNow("Esc×3") }
    }
}
serve()
app.run()
