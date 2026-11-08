// 键盘清洁模式：`open -n DeckHands.app --args --clean --seconds 90 --black 1 --mouse 0`
// 单独起一个 DeckHands 进程（和菜单栏那个互不影响），沿用 DeckHands 已有的「辅助功能」权限。
//
// 怎么锁：在最底层（HID）装一个事件拦截（CGEventTap），把按键、修饰键、亮度/音量等媒体键吞掉；
// 选了「连触控板一起锁」时，鼠标/触控板的移动、点击、滚动、手势也吞掉。
// 写法参考了两个 MIT 开源项目：sorkila/lockpaw（拦截被系统停用时马上重新打开、各种兜底）
// 和 kfv/keylock（HID 层 + defaultTap、回调里返回 nil 吞掉事件）。代码是自己重写的。
//
// 绝不能锁死，解锁的路一共六条，任何一条都行：
//   1. 屏幕上「按住解锁」按钮按住 1 秒（锁触控板时没有这条）
//   2. 同时按住左右两个 ⌘ 2 秒（期间不能按别的键，擦键盘时几乎不可能误触）
//   3. 倒计时到了自动解锁（默认 90 秒，最长 10 分钟）
//   4. Deck 工具箱里点「解锁」/ 对 Vesper 说「解锁键盘」（服务端给这个进程发 SIGTERM）
//   5. 合盖、睡眠、锁屏、切换用户：立刻解锁（不然登录界面输不了密码）
//   6. 进程不管因为什么退出或卡住，系统都会自动撤掉拦截：卡住时系统会停用超时的拦截，
//      另有一条后台线程到点直接 exit，不依赖主线程。
import AppKit
import ApplicationServices
import IOKit
import IOKit.hidsystem

// MARK: - 参数和状态文件

struct CleanOpts {
    var seconds = 90
    var black = true
    var mouse = false
    var glass = false        // Deck 皮肤：false = 新拟态，true = 液态玻璃
    var dark: Bool? = nil    // Deck 白天/夜里；nil = 跟系统

    static func parse(_ args: [String]) -> CleanOpts {
        var o = CleanOpts()
        func val(_ k: String) -> String? {
            guard let i = args.firstIndex(of: k), i + 1 < args.count else { return nil }
            return args[i + 1]
        }
        if let s = val("--seconds"), let n = Int(s) { o.seconds = n }
        if let s = val("--black") { o.black = s == "1" || s == "true" }
        if let s = val("--mouse") { o.mouse = s == "1" || s == "true" }
        if let s = val("--skin") { o.glass = s == "glass" }
        if let s = val("--theme") { o.dark = s == "dark" ? true : s == "light" ? false : nil }
        o.seconds = max(10, min(o.mouse ? 120 : 600, o.seconds))  // 锁触控板时最多 2 分钟
        return o
    }
}

let cleanStatusURL = supportDir.appendingPathComponent("clean.json")
let cleanMaxTotal: TimeInterval = 600  // 加时也不能超过 10 分钟

func writeCleanStatus(_ d: [String: Any]) {
    var d = d
    d["pid"] = Int(getpid())
    d["at"] = Date().timeIntervalSince1970
    guard let data = try? JSONSerialization.data(withJSONObject: d) else { return }
    try? data.write(to: cleanStatusURL, options: .atomic)
    chmod(cleanStatusURL.path, 0o600)
}

// MARK: - 截止时间（多线程共用）

final class Deadline {
    private let lock = NSLock()
    private var t: Date
    let started = Date()
    init(_ secs: Int) { t = Date().addingTimeInterval(TimeInterval(secs)) }
    var value: Date { lock.lock(); defer { lock.unlock() }; return t }
    func extend(_ s: TimeInterval) -> Date {
        lock.lock(); defer { lock.unlock() }
        t = min(t.addingTimeInterval(s), started.addingTimeInterval(cleanMaxTotal))
        return t
    }
}

// MARK: - 事件拦截

var cleanTap: CFMachPort?
var cleanMouseLocked = false
// 左右 ⌘ 同时按住的起点；按住期间按了别的键就作废。只在主线程（拦截回调就在主线程）读写。
var cmdBothSince: Date?
var keysDown = Set<Int64>()

let DEV_LCMD: UInt64 = 0x08, DEV_RCMD: UInt64 = 0x10
// 除了两个 ⌘ 以外的修饰键（左右 Shift / Ctrl / Option、Fn）。Caps Lock 是「锁定」状态，开着也不影响解锁，所以不算
let OTHER_MODS: CGEventFlags = [.maskShift, .maskControl, .maskAlternate, .maskSecondaryFn]

func cleanCallback(proxy: CGEventTapProxy, type: CGEventType, event: CGEvent, refcon: UnsafeMutableRawPointer?) -> Unmanaged<CGEvent>? {
    // 系统嫌回调太慢、或用户输入时把拦截停了：马上重新打开（lockpaw 的做法）
    if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
        if let t = cleanTap { CGEvent.tapEnable(tap: t, enable: true) }
        return Unmanaged.passUnretained(event)
    }
    switch type {
    case .keyDown:
        if event.getIntegerValueField(.keyboardEventAutorepeat) == 0 {
            keysDown.insert(event.getIntegerValueField(.keyboardEventKeycode))
        }
        cmdBothSince = nil
        return nil
    case .keyUp:
        keysDown.remove(event.getIntegerValueField(.keyboardEventKeycode))
        return nil
    case .flagsChanged:
        let raw = event.flags.rawValue
        let both = raw & DEV_LCMD != 0 && raw & DEV_RCMD != 0
        if both && event.flags.intersection(OTHER_MODS).isEmpty {
            if cmdBothSince == nil { cmdBothSince = Date() }
        } else {
            cmdBothSince = nil
        }
        return nil
    default:
        // 媒体键（亮度、音量、播放）是 NSSystemDefined = 14
        if type.rawValue == 14 { return nil }
        if cleanMouseLocked { return nil }
        return Unmanaged.passUnretained(event)
    }
}

func cleanMask(mouse: Bool, gestures: Bool) -> CGEventMask {
    var types: [UInt32] = [CGEventType.keyDown.rawValue, CGEventType.keyUp.rawValue, CGEventType.flagsChanged.rawValue, 14]
    if mouse {
        types += [CGEventType.leftMouseDown, .leftMouseUp, .rightMouseDown, .rightMouseUp, .mouseMoved,
                  .leftMouseDragged, .rightMouseDragged, .scrollWheel, .otherMouseDown, .otherMouseUp, .otherMouseDragged].map { $0.rawValue }
        // 触控板手势（缩放、轻扫、旋转、调度中心等）。不是公开常量，装不上就退回不带手势的版本
        if gestures { types += [18, 19, 20, 29, 30, 31] }
    }
    return types.reduce(CGEventMask(0)) { $0 | (CGEventMask(1) << CGEventMask($1)) }
}

func installCleanTap(mouse: Bool) -> Bool {
    cleanMouseLocked = mouse
    let tries: [CGEventMask] = mouse ? [cleanMask(mouse: true, gestures: true), cleanMask(mouse: true, gestures: false)]
                                     : [cleanMask(mouse: false, gestures: false)]
    for m in tries {
        if let t = CGEvent.tapCreate(tap: .cghidEventTap, place: .headInsertEventTap, options: .defaultTap,
                                     eventsOfInterest: m, callback: cleanCallback, userInfo: nil) {
            cleanTap = t
            let src = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, t, 0)
            CFRunLoopAddSource(CFRunLoopGetMain(), src, .commonModes)
            CGEvent.tapEnable(tap: t, enable: true)
            return true
        }
    }
    return false
}

// MARK: - Caps Lock：擦键盘时被按到会改变大写锁定，结束后恢复原样

func capsLock() -> Bool? {
    let svc = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching(kIOHIDSystemClass))
    guard svc != 0 else { return nil }
    defer { IOObjectRelease(svc) }
    var conn: io_connect_t = 0
    guard IOServiceOpen(svc, mach_task_self_, UInt32(kIOHIDParamConnectType), &conn) == KERN_SUCCESS else { return nil }
    defer { IOServiceClose(conn) }
    var on = false
    return IOHIDGetModifierLockState(conn, Int32(kIOHIDCapsLockState), &on) == KERN_SUCCESS ? on : nil
}
func setCapsLock(_ v: Bool) {
    let svc = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching(kIOHIDSystemClass))
    guard svc != 0 else { return }
    defer { IOObjectRelease(svc) }
    var conn: io_connect_t = 0
    guard IOServiceOpen(svc, mach_task_self_, UInt32(kIOHIDParamConnectType), &conn) == KERN_SUCCESS else { return }
    defer { IOServiceClose(conn) }
    IOHIDSetModifierLockState(conn, Int32(kIOHIDCapsLockState), v)
}

// MARK: - 画面（跟 Deck 的两套皮肤走：新拟态 / 液态玻璃；黑屏时两套都压暗，不抢灰尘的戏）

let cleanFont: (CGFloat, NSFont.Weight) -> NSFont = { size, w in
    let f = NSFont.systemFont(ofSize: size, weight: w)
    if let d = f.fontDescriptor.withDesign(.rounded), let r = NSFont(descriptor: d, size: size) { return r }
    return f
}

func rgb(_ hex: UInt32, _ a: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 0xff) / 255, green: CGFloat((hex >> 8) & 0xff) / 255, blue: CGFloat(hex & 0xff) / 255, alpha: a)
}

// 一套颜色。数值和 dashboard.html 里 html.skin-soft 的 --nm-* 一致
struct CleanLook {
    var glass: Bool
    var black: Bool
    var dark: Bool
    var bg: NSColor { black ? .black : dark ? rgb(0x252a33) : rgb(0xe3e8ef) }
    var text: NSColor { black ? NSColor(white: 0.62, alpha: 1) : glass ? .labelColor : dark ? rgb(0xeceff5) : rgb(0x141925) }
    // 玻璃底会透出桌面，副文字用系统次级色太淡（白天对比度不够），改成 78% 的主文字色
    var text2: NSColor { black ? NSColor(white: 0.40, alpha: 1) : glass ? NSColor.labelColor.withAlphaComponent(0.78) : dark ? rgb(0xa9b0bf) : rgb(0x465062) }
    var hi: NSColor { black ? NSColor(white: 1, alpha: 0.07) : dark ? rgb(0x383f4d, 0.9) : NSColor(white: 1, alpha: 0.95) }
    var lo: NSColor { black ? NSColor(white: 0, alpha: 1) : dark ? rgb(0x0a0c10, 0.75) : rgb(0x92a0ba, 0.58) }
    var face: NSColor { black ? NSColor(white: 0.075, alpha: 1) : bg }
    var track: NSColor { black ? NSColor(white: 0.16, alpha: 1) : glass ? NSColor.labelColor.withAlphaComponent(0.14) : dark ? NSColor(white: 0, alpha: 0.28) : rgb(0x92a0ba, 0.22) }
    var accent: NSColor { .systemGreen }
}

// 按住 1 秒解锁的圆按钮：外圈跟着按住的进度走，中途松开就退回。
// 新拟态：凸起的圆，按下去变凹；液态玻璃：按钮本身透明，外面套一层系统玻璃（见 CleanPanelView）
final class HoldButton: NSView {
    var onDone: (() -> Void)?
    var look = CleanLook(glass: false, black: true, dark: true)
    private var progress: CGFloat = 0
    private var pressed = false
    private var timer: Timer?
    private var downAt: Date?
    let holdTime: TimeInterval = 1.0

    override var isFlipped: Bool { false }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func resetCursorRects() { addCursorRect(bounds, cursor: .pointingHand) }

    override func mouseDown(with event: NSEvent) {
        downAt = Date(); pressed = true
        timer?.invalidate()
        let t = Timer(timeInterval: 1.0 / 60, repeats: true) { [weak self] _ in
            guard let self, let d = self.downAt else { return }
            self.progress = min(1, CGFloat(Date().timeIntervalSince(d) / self.holdTime))
            self.needsDisplay = true
            if self.progress >= 1 { self.timer?.invalidate(); self.downAt = nil; self.onDone?() }
        }
        timer = t
        RunLoop.main.add(t, forMode: .common)
    }
    override func mouseUp(with event: NSEvent) {
        downAt = nil; pressed = false
        timer?.invalidate()
        let t = Timer(timeInterval: 1.0 / 60, repeats: true) { [weak self] t in
            guard let self else { t.invalidate(); return }
            self.progress = max(0, self.progress - 0.08)
            self.needsDisplay = true
            if self.progress <= 0 { t.invalidate() }
        }
        timer = t
        RunLoop.main.add(t, forMode: .common)
    }

    override func draw(_ dirtyRect: NSRect) {
        let r = bounds.insetBy(dx: 12, dy: 12)
        let circle = NSBezierPath(ovalIn: r)
        if !look.glass {
            NSGraphicsContext.saveGraphicsState()
            if pressed {
                look.face.setFill(); circle.fill()
                // 凹下去：内阴影（右下亮、左上暗）
                circle.addClip()
                for (c, dx, dy) in [(look.lo, CGFloat(4), CGFloat(-4)), (look.hi, CGFloat(-4), CGFloat(4))] {
                    let sh = NSShadow(); sh.shadowColor = c; sh.shadowBlurRadius = 7; sh.shadowOffset = NSSize(width: dx, height: dy)
                    sh.set()
                    let ring = NSBezierPath(rect: r.insetBy(dx: -20, dy: -20)); ring.append(circle.reversed)
                    look.face.setFill(); ring.fill()
                }
            } else {
                // 凸起来：左上亮影、右下暗影
                for (c, dx, dy) in [(look.hi, CGFloat(-5), CGFloat(5)), (look.lo, CGFloat(5), CGFloat(-5))] {
                    let sh = NSShadow(); sh.shadowColor = c; sh.shadowBlurRadius = 10; sh.shadowOffset = NSSize(width: dx, height: dy)
                    sh.set(); look.face.setFill(); circle.fill()
                }
            }
            NSGraphicsContext.restoreGraphicsState()
        }
        // 进度外圈
        let rr = r.insetBy(dx: 5, dy: 5)
        let track = NSBezierPath(ovalIn: rr); track.lineWidth = 3
        look.track.setStroke(); track.stroke()
        if progress > 0 {
            let arc = NSBezierPath()
            arc.appendArc(withCenter: NSPoint(x: bounds.midX, y: bounds.midY), radius: rr.width / 2, startAngle: 90, endAngle: 90 - 360 * progress, clockwise: true)
            arc.lineWidth = 3; arc.lineCapStyle = .round
            look.accent.setStroke(); arc.stroke()
        }
        // 锁的图标 + 文字
        let fg = look.text
        let s = NSAttributedString(string: progress > 0 ? "继续按住" : "按住解锁", attributes: [.font: cleanFont(12.5, .medium), .foregroundColor: fg])
        let sz = s.size()
        if let img = NSImage(systemSymbolName: progress > 0 ? "lock.open.fill" : "lock.fill", accessibilityDescription: nil)?
            .withSymbolConfiguration(.init(pointSize: 18, weight: .medium)) {
            let tinted = NSImage(size: img.size, flipped: false) { rect in img.draw(in: rect); fg.set(); rect.fill(using: .sourceAtop); return true }
            tinted.draw(at: NSPoint(x: bounds.midX - img.size.width / 2, y: bounds.midY + 3), from: .zero, operation: .sourceOver, fraction: 1)
        }
        s.draw(at: NSPoint(x: bounds.midX - sz.width / 2, y: bounds.midY - sz.height - 3))
    }
}

// 能当主窗口、吞掉一切按键的窗口：万一拦截没拦住，按键也只会落到这里，不会打进别的应用
final class CleanWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
    override func keyDown(with event: NSEvent) {}
    override func keyUp(with event: NSEvent) {}
    override func flagsChanged(with event: NSEvent) {}
    override func performKeyEquivalent(with event: NSEvent) -> Bool { true }
    override func cancelOperation(_ sender: Any?) {}
}

// 面板内容：标题、倒计时、按住解锁、提示。宽 380；高度：锁键盘 300，连触控板 180（没有按钮）
let CLEAN_W: CGFloat = 380
func cleanPanelH(_ mouse: Bool) -> CGFloat { mouse ? 180 : 300 }

final class CleanPanelView: NSView {
    let title = NSTextField(labelWithString: "")
    let timeLabel = NSTextField(labelWithString: "")
    let hint = NSTextField(labelWithString: "")
    let hold = HoldButton()

    init(look: CleanLook, mouse: Bool) {
        super.init(frame: NSRect(x: 0, y: 0, width: CLEAN_W, height: cleanPanelH(mouse)))
        title.stringValue = mouse ? "键盘和触控板已锁定" : "键盘已锁定"
        title.font = cleanFont(16, .semibold); title.textColor = look.glass && !look.black ? look.text : look.text2
        let tf = NSFont.monospacedDigitSystemFont(ofSize: 46, weight: .light)
        timeLabel.font = tf.fontDescriptor.withDesign(.rounded).flatMap { NSFont(descriptor: $0, size: 46) } ?? tf
        timeLabel.textColor = look.text
        hint.font = cleanFont(12.5, .regular); hint.textColor = look.text2
        hint.maximumNumberOfLines = 4
        hint.lineBreakMode = .byWordWrapping
        hint.preferredMaxLayoutWidth = CLEAN_W - 56
        hint.stringValue = mouse
            ? "同时按住左右 ⌘ 2 秒解锁，或等倒计时结束\n也可以在 Deck 里点解锁、对 Vesper 说「解锁键盘」"
            : "按住上面的按钮 1 秒，或同时按住左右 ⌘ 2 秒解锁\n倒计时结束也会自动解锁，放心擦"
        for v in [title, timeLabel, hint] { v.alignment = .center; v.translatesAutoresizingMaskIntoConstraints = false; addSubview(v) }
        hold.look = look
        hold.translatesAutoresizingMaskIntoConstraints = false
        var holdHost: NSView = hold
        if look.glass, !look.black, #available(macOS 26.0, *) {
            // 液态玻璃：按钮外面套一层系统玻璃圆片
            let g = NSGlassEffectView()
            g.cornerRadius = 41
            g.contentView = hold
            g.translatesAutoresizingMaskIntoConstraints = false
            holdHost = g
        }
        holdHost.isHidden = mouse
        addSubview(holdHost)
        NSLayoutConstraint.activate([
            title.centerXAnchor.constraint(equalTo: centerXAnchor),
            title.topAnchor.constraint(equalTo: topAnchor, constant: 22),
            timeLabel.centerXAnchor.constraint(equalTo: centerXAnchor),
            timeLabel.topAnchor.constraint(equalTo: title.bottomAnchor, constant: 2),
            holdHost.centerXAnchor.constraint(equalTo: centerXAnchor),
            holdHost.topAnchor.constraint(equalTo: timeLabel.bottomAnchor, constant: 6),
            holdHost.widthAnchor.constraint(equalToConstant: mouse ? 0 : 106),
            holdHost.heightAnchor.constraint(equalToConstant: mouse ? 0 : 106),
            hint.topAnchor.constraint(equalTo: holdHost.bottomAnchor, constant: mouse ? 8 : 10),
            hint.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 28),
            hint.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -28),
        ])
        if holdHost !== hold {  // 玻璃里的按钮铺满玻璃
            NSLayoutConstraint.activate([
                hold.leadingAnchor.constraint(equalTo: holdHost.leadingAnchor), hold.trailingAnchor.constraint(equalTo: holdHost.trailingAnchor),
                hold.topAnchor.constraint(equalTo: holdHost.topAnchor), hold.bottomAnchor.constraint(equalTo: holdHost.bottomAnchor),
            ])
        }
    }
    required init?(coder: NSCoder) { nil }
}

// 新拟态的面板底：和 Deck 卡片一样的底色 + 左上亮影、右下暗影（两层影子各画一层）
final class SoftPlate: NSView {
    init(frame: NSRect, look: CleanLook, radius: CGFloat) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.masksToBounds = false
        for (c, dx, dy) in [(look.lo, CGFloat(9), CGFloat(-9)), (look.hi, CGFloat(-9), CGFloat(9))] {
            let l = CALayer()
            l.frame = bounds
            l.cornerRadius = radius
            l.backgroundColor = look.bg.cgColor
            l.shadowColor = c.cgColor
            l.shadowOpacity = 1
            l.shadowRadius = 13
            l.shadowOffset = CGSize(width: dx, height: dy)
            layer?.addSublayer(l)
        }
        let top = CALayer()
        top.frame = bounds; top.cornerRadius = radius; top.backgroundColor = look.bg.cgColor
        top.borderWidth = 1; top.borderColor = look.hi.withAlphaComponent(0.6).cgColor
        layer?.addSublayer(top)
    }
    required init?(coder: NSCoder) { nil }
}

// MARK: - 清洁模式主体

final class CleanSession: NSObject {
    let opts: CleanOpts
    let deadline: Deadline
    var windows: [NSWindow] = []
    var panels: [CleanPanelView] = []
    var ticker: Timer?
    var capsBefore: Bool?
    var activity: NSObjectProtocol?
    var finished = false
    var lockFD: Int32 = -1
    var lastBeat = Date()

    init(_ o: CleanOpts) { opts = o; deadline = Deadline(o.seconds) }

    func fail(_ msg: String) -> Never {
        log("clean: \(msg)")
        writeCleanStatus(["state": "error", "error": msg])
        exit(2)
    }

    func start() {
        // 同一时间只允许一个清洁进程
        lockFD = open(supportDir.appendingPathComponent("clean.lock").path, O_CREAT | O_RDWR, 0o600)
        if lockFD < 0 || flock(lockFD, LOCK_EX | LOCK_NB) != 0 { log("clean: already running"); exit(0) }
        guard AXIsProcessTrusted() else {
            fail("DeckHands 还没有辅助功能权限：系统设置 → 隐私与安全性 → 辅助功能，打开 DeckHands")
        }
        capsBefore = capsLock()
        guard installCleanTap(mouse: opts.mouse) else {
            fail("没能拦住键盘（系统拒绝了）。去系统设置 → 隐私与安全性 → 辅助功能，把 DeckHands 关掉再打开一次")
        }
        log("clean: locked \(opts.seconds)s black=\(opts.black) mouse=\(opts.mouse)")
        activity = ProcessInfo.processInfo.beginActivity(options: [.idleDisplaySleepDisabled, .userInitiated], reason: "键盘清洁")
        buildWindows()
        watchSystem()
        startWatchdog()
        status()
        ticker = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in self?.tick() }
        RunLoop.main.add(ticker!, forMode: .common)
        tick()
    }

    func status() {
        writeCleanStatus(["state": "locked", "until": deadline.value.timeIntervalSince1970, "started": deadline.started.timeIntervalSince1970,
                          "black": opts.black, "mouse": opts.mouse, "max": deadline.started.timeIntervalSince1970 + cleanMaxTotal, "beat": true])
    }

    func systemDark() -> Bool {
        NSApplication.shared.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
    }

    func buildWindows() {
        let app = NSApplication.shared
        let dark = opts.dark ?? systemDark()
        let look = CleanLook(glass: opts.glass, black: opts.black, dark: opts.black ? true : dark)
        let ph = cleanPanelH(opts.mouse)
        func makeWindow(_ frame: NSRect) -> CleanWindow {
            let w = CleanWindow(contentRect: frame, styleMask: .borderless, backing: .buffered, defer: false)
            w.setFrame(frame, display: false)
            w.level = .screenSaver
            w.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
            w.isReleasedWhenClosed = false
            w.appearance = NSAppearance(named: (opts.black || dark) ? .darkAqua : .aqua)
            return w
        }
        if opts.black {
            // 每块屏幕一张全黑的窗，盖住菜单栏和程序坞，灰尘和指纹看得最清楚
            for scr in NSScreen.screens {
                let w = makeWindow(scr.frame)
                w.backgroundColor = .black
                w.isOpaque = true
                w.hasShadow = false
                let root = NSView(frame: NSRect(origin: .zero, size: scr.frame.size))
                let p = CleanPanelView(look: look, mouse: opts.mouse)
                p.frame.origin = NSPoint(x: (scr.frame.width - CLEAN_W) / 2, y: (scr.frame.height - ph) / 2)
                p.autoresizingMask = [.minXMargin, .maxXMargin, .minYMargin, .maxYMargin]
                root.addSubview(p)
                p.hold.onDone = { [weak self] in self?.finish("button") }
                w.contentView = root
                windows.append(w); panels.append(p)
            }
        } else {
            // 不黑屏：主屏底部居中一块面板，样子跟 Deck 皮肤走
            guard let scr = NSScreen.main ?? NSScreen.screens.first else { return }
            let margin: CGFloat = 30   // 影子（新拟态的双影子、玻璃自带的影子）画在面板外面，窗口四周留出边
            let vf = scr.visibleFrame
            let frame = NSRect(x: vf.midX - CLEAN_W / 2 - margin, y: vf.minY + 36 - margin, width: CLEAN_W + margin * 2, height: ph + margin * 2)
            let w = makeWindow(frame)
            w.isOpaque = false
            w.backgroundColor = .clear
            // 不要窗口自己的影子：透明窗的影子按整块矩形算，玻璃圆角外会露出方形暗边（玻璃自己会画影子）
            w.hasShadow = false
            let p = CleanPanelView(look: look, mouse: opts.mouse)
            p.hold.onDone = { [weak self] in self?.finish("button") }
            let root = NSView(frame: NSRect(origin: .zero, size: frame.size))
            let plateRect = NSRect(x: margin, y: margin, width: CLEAN_W, height: ph)
            if opts.glass {
                if #available(macOS 26.0, *) {
                    let g = NSGlassEffectView(frame: plateRect)
                    g.cornerRadius = 28
                    // 给玻璃一点底色，文字压在花哨的桌面上也看得清
                    g.tintColor = dark ? NSColor(white: 0, alpha: 0.28) : NSColor(white: 1, alpha: 0.5)
                    g.contentView = p
                    root.addSubview(g)
                } else {
                    let fx = NSVisualEffectView(frame: plateRect)
                    fx.material = .hudWindow; fx.blendingMode = .behindWindow; fx.state = .active
                    fx.wantsLayer = true; fx.layer?.cornerRadius = 28; fx.layer?.masksToBounds = true
                    p.frame = fx.bounds
                    fx.addSubview(p)
                    root.addSubview(fx)
                }
            } else {
                root.addSubview(SoftPlate(frame: plateRect, look: look, radius: 28))
                p.frame = plateRect
                root.addSubview(p)
            }
            w.contentView = root
            windows.append(w); panels.append(p)
        }
        app.activate(ignoringOtherApps: true)
        for w in windows { w.makeKeyAndOrderFront(nil) }
        windows.first?.makeKey()
    }

    func tick() {
        let left = deadline.value.timeIntervalSinceNow
        if left <= 0 { return finish("timer") }
        // 心跳：主线程每 2 秒写一次状态；服务端看到心跳停了 6 秒就当卡死，直接结束进程（kbclean.py）
        if Date().timeIntervalSince(lastBeat) >= 2 { lastBeat = Date(); status() }
        var holding: TimeInterval = 0
        // 漏掉了松开事件的键（比如拦截被系统短暂停用时）按硬件状态清掉，免得 ⌘⌘ 永远解不了
        keysDown = keysDown.filter { CGEventSource.keyState(.hidSystemState, key: CGKeyCode(truncatingIfNeeded: $0)) }
        if let s = cmdBothSince, keysDown.isEmpty {
            holding = Date().timeIntervalSince(s)
            if holding >= 2 { return finish("cmd+cmd") }
        }
        let secs = Int(left.rounded(.up))
        let text = holding > 0 ? "解锁中 \(Int(holding * 50))%" : String(format: "%d:%02d", secs / 60, secs % 60)
        for p in panels where p.timeLabel.stringValue != text { p.timeLabel.stringValue = text }
        // 别的应用把自己顶到前面时（比如通知点开了某个应用），把焦点抢回来
        if !NSApplication.shared.isActive { NSApplication.shared.activate(ignoringOtherApps: true) }
    }

    func watchSystem() {
        let ws = NSWorkspace.shared.notificationCenter
        for n in [NSWorkspace.willSleepNotification, NSWorkspace.screensDidSleepNotification, NSWorkspace.sessionDidResignActiveNotification] {
            ws.addObserver(forName: n, object: nil, queue: .main) { [weak self] _ in self?.finish("sleep/lock") }
        }
        DistributedNotificationCenter.default().addObserver(forName: Notification.Name("com.apple.screenIsLocked"), object: nil, queue: .main) { [weak self] _ in
            self?.finish("screen locked")
        }
        NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main) { [weak self] _ in
            // 接上/拔掉 iPad 或显示器：重新铺一遍窗口
            guard let self, !self.finished else { return }
            for w in self.windows { w.orderOut(nil) }
            self.windows = []; self.panels = []
            self.buildWindows()
        }
        // Deck 解锁：SIGTERM；Deck 加时：SIGUSR1（+30 秒）
        signal(SIGTERM, SIG_IGN)
        signal(SIGUSR1, SIG_IGN)
        let term = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        term.setEventHandler { [weak self] in self?.finish("deck") }
        term.resume()
        let usr = DispatchSource.makeSignalSource(signal: SIGUSR1, queue: .main)
        usr.setEventHandler { [weak self] in
            guard let self else { return }
            _ = self.deadline.extend(30)
            self.status()
            log("clean: +30s")
        }
        usr.resume()
        sources = [term, usr]
    }
    var sources: [DispatchSourceSignal] = []

    // 不依赖主线程的兜底：到点（截止时间 + 3 秒）直接退出进程，系统随之撤掉拦截
    func startWatchdog() {
        let dl = deadline
        Thread.detachNewThread {
            while true {
                Thread.sleep(forTimeInterval: 0.5)
                if Date() > dl.value.addingTimeInterval(3) || Date() > dl.started.addingTimeInterval(cleanMaxTotal + 5) {
                    if let t = cleanTap { CGEvent.tapEnable(tap: t, enable: false) }
                    writeCleanStatus(["state": "done", "reason": "watchdog"])
                    log("clean: watchdog exit")
                    exit(0)
                }
            }
        }
    }

    func finish(_ why: String) {
        if finished { return }
        finished = true
        if let t = cleanTap { CGEvent.tapEnable(tap: t, enable: false) }
        ticker?.invalidate()
        if let before = capsBefore, let now = capsLock(), now != before { setCapsLock(before) }
        for w in windows { w.orderOut(nil) }
        if let a = activity { ProcessInfo.processInfo.endActivity(a) }
        writeCleanStatus(["state": "done", "reason": why])
        log("clean: unlocked (\(why))")
        exit(0)
    }
}

var cleanSession: CleanSession?

func runCleanMode() -> Never {
    signal(SIGPIPE, SIG_IGN)
    let app = NSApplication.shared
    app.setActivationPolicy(.accessory)
    let s = CleanSession(CleanOpts.parse(CommandLine.arguments))
    cleanSession = s
    s.start()
    app.run()
    exit(0)
}
