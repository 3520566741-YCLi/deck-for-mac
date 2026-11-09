import Carbon
import Foundation

/// 全局快捷键（Carbon RegisterEventHotKey：不需要辅助功能权限）
///   ⌃⌥⌘M 麦克风开/关   ⌃⌥⌘J 或 F5 直接说（轻按：说完自动结束；按住：松手就结束）   ⌃⌥⌘. 或 F6 停
final class HotKeys {
    static let shared = HotKeys()
    var handler: ((String) -> Void)?
    /// 按下/松开（给「按住说话」用）：(名字, 是否按下)
    var pressHandler: ((String, Bool) -> Void)?
    private var refs: [EventHotKeyRef?] = []
    // 外接键盘（无 Touch ID 款）的 F5 / F6 本来没有功能，直接当单键用：F5 按住说话，F6 停。不需要任何额外权限。
    private let names = ["mic", "talk", "stop", "talk", "stop"]

    func register() {
        var specs = [EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed)),
                     EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyReleased))]
        InstallEventHandler(GetApplicationEventTarget(), { _, event, _ -> OSStatus in
            var hk = EventHotKeyID()
            GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                              nil, MemoryLayout<EventHotKeyID>.size, nil, &hk)
            let i = Int(hk.id)
            guard i < HotKeys.shared.names.count else { return noErr }
            let name = HotKeys.shared.names[i]
            let pressed = GetEventKind(event) == UInt32(kEventHotKeyPressed)
            if pressed { HotKeys.shared.handler?(name) }
            HotKeys.shared.pressHandler?(name, pressed)
            return noErr
        }, 2, &specs, nil, nil)
        let cmdOpt = UInt32(controlKey | cmdKey | optionKey)  // 11-09 加 ⌃：⌥⌘J 会抢 Chrome 控制台、⌥⌘M 会抢系统「全部最小化」
        let useF = UserDefaults.standard.object(forKey: "fKeys") as? Bool ?? false
        var keys: [(Int, UInt32)] = [(kVK_ANSI_M, cmdOpt), (kVK_ANSI_J, cmdOpt), (kVK_ANSI_Period, cmdOpt)]
        if useF {
            keys.append((kVK_F5, UInt32(0)))
            keys.append((kVK_F6, UInt32(0)))
        }
        for (i, (k, mods)) in keys.enumerated() {
            var ref: EventHotKeyRef?
            let id = EventHotKeyID(signature: OSType(0x4445_4B56), id: UInt32(i))  // 'DEKV'
            RegisterEventHotKey(UInt32(k), mods, id, GetApplicationEventTarget(), 0, &ref)
            refs.append(ref)
        }
    }
}
