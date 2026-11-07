import CoreAudio
import Foundation

/// 选音频设备：所有声音都走 Mac。跟随系统默认，但跳过 iPad / OpenDisplay / Sidecar / AirPlay 这类扩展屏带出来的虚拟设备，
/// 默认设备是这类时退回 Mac 自带的麦克风 / 扬声器。设备增减或默认设备变化时回调，由 AudioCore 重新选。
enum Devices {
    private static let badNames = ["ipad", "opendisplay", "sidecar", "airplay", "display audio", "luna", "duet"]
    private static let badTransports: Set<UInt32> = [kAudioDeviceTransportTypeAirPlay, kAudioDeviceTransportTypeDisplayPort,
                                                     kAudioDeviceTransportTypeHDMI, kAudioDeviceTransportTypeVirtual]

    static func prop<T>(_ id: AudioObjectID, _ sel: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal, _ def: T) -> T {
        var addr = AudioObjectPropertyAddress(mSelector: sel, mScope: scope, mElement: kAudioObjectPropertyElementMain)
        var v = def
        var size = UInt32(MemoryLayout<T>.size)
        let st = withUnsafeMutablePointer(to: &v) { AudioObjectGetPropertyData(id, &addr, 0, nil, &size, $0) }
        return st == noErr ? v : def
    }

    static func name(_ id: AudioDeviceID) -> String {
        var addr = AudioObjectPropertyAddress(mSelector: kAudioObjectPropertyName, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var cf: Unmanaged<CFString>?
        var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &cf) == noErr, let s = cf?.takeRetainedValue() else { return "" }
        return s as String
    }

    static func all() -> [AudioDeviceID] {
        var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size) == noErr else { return [] }
        var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &ids) == noErr else { return [] }
        return ids
    }

    static func hasStreams(_ id: AudioDeviceID, input: Bool) -> Bool {
        var addr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreams,
                                              mScope: input ? kAudioDevicePropertyScopeInput : kAudioDevicePropertyScopeOutput,
                                              mElement: kAudioObjectPropertyElementMain)
        var size: UInt32 = 0
        return AudioObjectGetPropertyDataSize(id, &addr, 0, nil, &size) == noErr && size > 0
    }

    /// 蓝牙耳机（AirPods）、iPhone 连续互通麦克风：只排除做麦克风。
    /// 11-07 它连上又断开时引擎启动失败（560227702）还连带崩溃；蓝牙麦克风还会把耳机切到低音质通话模式。
    private static let badInputTransports: Set<UInt32> = [kAudioDeviceTransportTypeBluetooth, kAudioDeviceTransportTypeBluetoothLE,
                                                          0x63637764 /* 'ccwd' 连续互通有线 */, 0x6363776C /* 'ccwl' 连续互通无线 */]

    static func isBad(_ id: AudioDeviceID, input: Bool = false) -> Bool {
        let n = name(id).lowercased()
        if badNames.contains(where: { n.contains($0) }) { return true }
        let t = prop(id, kAudioDevicePropertyTransportType, kAudioObjectPropertyScopeGlobal, UInt32(0))
        if input && badInputTransports.contains(t) { return true }
        return badTransports.contains(t)
    }

    /// 返回要用的设备；nil 表示「用系统默认就好」
    static func pick(input: Bool) -> AudioDeviceID? {
        let sel = input ? kAudioHardwarePropertyDefaultInputDevice : kAudioHardwarePropertyDefaultOutputDevice
        let def = prop(AudioObjectID(kAudioObjectSystemObject), sel, kAudioObjectPropertyScopeGlobal, AudioDeviceID(0))
        if def != 0 && !isBad(def, input: input) { return nil }
        let candidates = all().filter { hasStreams($0, input: input) && !isBad($0, input: input) }
        let builtIn = candidates.first { prop($0, kAudioDevicePropertyTransportType, kAudioObjectPropertyScopeGlobal, UInt32(0)) == kAudioDeviceTransportTypeBuiltIn }
        return builtIn ?? candidates.first
    }

    static func describe(input: Bool) -> String {
        if let id = pick(input: input) { return name(id) }
        let sel = input ? kAudioHardwarePropertyDefaultInputDevice : kAudioHardwarePropertyDefaultOutputDevice
        return name(prop(AudioObjectID(kAudioObjectSystemObject), sel, kAudioObjectPropertyScopeGlobal, AudioDeviceID(0)))
    }

    /// 「该用哪个麦克风/扬声器」的指纹，只有它变了才需要重建引擎
    static func signature() -> String {
        func id(_ input: Bool) -> AudioDeviceID {
            if let d = pick(input: input) { return d }
            let sel = input ? kAudioHardwarePropertyDefaultInputDevice : kAudioHardwarePropertyDefaultOutputDevice
            return prop(AudioObjectID(kAudioObjectSystemObject), sel, kAudioObjectPropertyScopeGlobal, AudioDeviceID(0))
        }
        return "\(id(true))/\(id(false))"
    }

    // ---------- 别的 App 在用麦克风吗（macOS 14.2+ 的进程级音频对象）----------

    /// 系统常驻、一直开着麦克风的进程，不算「别的 App 在用」
    private static let ignoredBundles = ["com.apple.corespeechd", "com.apple.siri", "com.apple.audio", "com.apple.CoreSpeech",
                                         "io.github.deckformac.deckvoice", "com.apple.controlcenter", "com.apple.accessibility"]

    /// 返回正在录音的其他 App 的 bundle id（没有就是空）
    static func otherAppsUsingMic() -> [String] {
        guard #available(macOS 14.2, *) else { return [] }
        var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyProcessObjectList, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size) == noErr, size > 0 else { return [] }
        var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &ids) == noErr else { return [] }
        let me = getpid()
        var out: [String] = []
        for p in ids {
            guard prop(p, kAudioProcessPropertyIsRunningInput, kAudioObjectPropertyScopeGlobal, UInt32(0)) != 0 else { continue }
            let pid = prop(p, kAudioProcessPropertyPID, kAudioObjectPropertyScopeGlobal, pid_t(0))
            if pid == me { continue }
            var baddr = AudioObjectPropertyAddress(mSelector: kAudioProcessPropertyBundleID, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
            var cf: Unmanaged<CFString>?
            var bsize = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
            let bid = AudioObjectGetPropertyData(p, &baddr, 0, nil, &bsize, &cf) == noErr ? (cf?.takeRetainedValue() as String? ?? "") : ""
            if ignoredBundles.contains(where: { bid.hasPrefix($0) }) { continue }
            out.append(bid.isEmpty ? "pid \(pid)" : bid)
        }
        return out
    }

    /// 设备插拔、默认设备变化
    static func watch(_ cb: @escaping () -> Void) {
        for sel in [kAudioHardwarePropertyDevices, kAudioHardwarePropertyDefaultInputDevice, kAudioHardwarePropertyDefaultOutputDevice] {
            var addr = AudioObjectPropertyAddress(mSelector: sel, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
            AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &addr, DispatchQueue.main) { _, _ in cb() }
        }
    }
}
