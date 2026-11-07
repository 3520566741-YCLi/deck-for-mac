import AppKit
import CoreGraphics
import Foundation

/// 单键「按住说话」（外接键盘右边的 ⌥ Option 等）：按住说、松手出结果。
/// 修饰键单独按下没法用 RegisterEventHotKey，只能用只读的事件监听（CGEventTap listenOnly），
/// 需要「系统设置 → 隐私与安全性 → 输入监控」里允许 DeckVoice。只看这一个键按没按下，不记录、不改任何按键。
/// 按住时如果又按了别的键（拿它当组合键用），就不算说话。
final class TalkKey {
    enum Key: String { case rightOption, rightCommand, none }
    var onDown: (() -> Void)?      // 按住超过 0.25 秒且没按别的键
    var onUp: (() -> Void)?
    var onStop: (() -> Void)?      // 另一侧右修饰键单独轻按一下 = 停
    var log: ((String) -> Void)?

    private var tap: CFMachPort?
    private var down = false
    private var chord = false
    private var armed = false
    private var downAt = Date.distantPast
    private(set) var key: Key = .none

    private var keyCode: Int64 { key == .rightOption ? 61 : 54 }
    private var deviceMask: UInt64 { key == .rightOption ? 0x40 : 0x10 }  // NX_DEVICERALTKEYMASK / NX_DEVICERCMDKEYMASK
    private var stopCode: Int64 { key == .rightOption ? 54 : 61 }
    private var stopMask: UInt64 { key == .rightOption ? 0x10 : 0x40 }
    private var sDown = false
    private var sChord = false
    private var sAt = Date.distantPast

    func start(_ k: Key) {
        stop()
        key = k
        guard k != .none else { return }
        if !CGPreflightListenEventAccess() {
            CGRequestListenEventAccess()
            log?("右侧按键说话需要「输入监控」权限：系统设置 → 隐私与安全性 → 输入监控 → 打开 DeckVoice，然后重开 DeckVoice")
        }
        let mask = (1 << CGEventType.flagsChanged.rawValue) | (1 << CGEventType.keyDown.rawValue)
        let me = Unmanaged.passUnretained(self).toOpaque()
        tap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .tailAppendEventTap, options: .listenOnly,
                                eventsOfInterest: CGEventMask(mask), callback: { _, type, event, ref in
            guard let ref = ref else { return Unmanaged.passUnretained(event) }
            Unmanaged<TalkKey>.fromOpaque(ref).takeUnretainedValue().handle(type, event)
            return Unmanaged.passUnretained(event)
        }, userInfo: me)
        guard let tap = tap else { log?("按键说话没开起来（多半是还没给「输入监控」权限）"); return }
        let src = CFMachPortCreateRunLoopSource(nil, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), src, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
        log?(k == .rightOption ? "按住右侧 ⌥ 说话、轻按右侧 ⌘ 停，已开启" : "按住右侧 ⌘ 说话、轻按右侧 ⌥ 停，已开启")
    }

    func stop() {
        if let t = tap { CGEvent.tapEnable(tap: t, enable: false) }
        tap = nil
    }

    private func handle(_ type: CGEventType, _ e: CGEvent) {
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            if let t = tap { CGEvent.tapEnable(tap: t, enable: true) }
            return
        }
        if type == .keyDown {
            if down { chord = true }  // 拿它当组合键用了
            if sDown { sChord = true }
            return
        }
        guard type == .flagsChanged else { return }
        let code = e.getIntegerValueField(.keyboardEventKeycode)
        if code == stopCode {
            let isDown = (e.flags.rawValue & stopMask) != 0
            if isDown && !sDown { sDown = true; sChord = false; sAt = Date() }
            else if !isDown && sDown {
                sDown = false
                if !sChord && Date().timeIntervalSince(sAt) < 0.6 { onStop?() }
            }
            return
        }
        guard code == keyCode else { return }
        let isDown = (e.flags.rawValue & deviceMask) != 0
        if isDown && !down {
            down = true; chord = false; armed = false
            let t = Date(); downAt = t
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in
                guard let self = self, self.down, !self.chord, self.downAt == t else { return }
                self.armed = true
                self.onDown?()
            }
        } else if !isDown && down {
            down = false
            if armed { armed = false; onUp?() }
        }
    }
}
