import AppKit
import AVFoundation
import Foundation

/// DeckVoice：Deck 语音层的小助手（菜单栏常驻、无 Dock 图标）。
///   - 唯一占用麦克风的进程：回声消除后的 16 kHz 音频送给 Python 语音服务（唤醒、声纹、定稿识别、「停」）
///   - Apple 本机实时字幕
///   - 播放 TTS（同一个音频引擎，回声消除才干净）
///   - 全局快捷键、拉起并守护 Python 服务
final class App: NSObject, NSApplicationDelegate {
    let link = Link()
    let audio = AudioCore()
    let captions = LiveCaptions()
    let talkKey = TalkKey()
    var service: Process?
    var serviceFails = 0
    var nextServiceTry = Date.distantPast
    var listening = false
    var status: NSStatusItem!
    var micOn = UserDefaults.standard.object(forKey: "micOn") as? Bool ?? true
    var micAllowed = false
    /// 别的 App（会议、录音、微信语音）在用麦克风时自动让出，用完再接着听
    var yieldMic = UserDefaults.standard.object(forKey: "yieldMic") as? Bool ?? false
    var yieldedTo: [String] = []

    var repo: String {
        (Bundle.main.object(forInfoDictionaryKey: "DeckRepoPath") as? String).map { ($0 as NSString).expandingTildeInPath }
            ?? (NSHomeDirectory() + "/deck-for-mac")
    }

    func applicationDidFinishLaunching(_ n: Notification) {
        let log: (String) -> Void = { [weak self] s in self?.link.log(s) }
        audio.log = log
        captions.log = log
        audio.onMic16k = { [weak self] s in
            guard let self = self else { return }
            let d = s.withUnsafeBufferPointer { Data(buffer: $0) }  // Apple 芯片本来就是小端 float32
            self.link.send(kind: "A", d)
        }
        audio.onNativeBuffer = { [weak self] b in if self?.listening == true { self?.captions.append(b) } }
        audio.onPlaybackMark = { [weak self] id in self?.link.send(json: ["t": "playback_done", "id": id]) }
        audio.onAEC = { [weak self] on in self?.link.send(json: ["t": "aec", "on": on]) }
        captions.onPartial = { [weak self] t in self?.link.send(json: ["t": "partial", "text": t]) }
        captions.onFinal = { [weak self] t in self?.link.send(json: ["t": "apple_final", "text": t]) }

        link.onConnected = { [weak self] in
            if let a = self?.audio { self?.link.send(json: ["t": "aec", "on": a.aecOn]) }
            guard let self = self else { return }
            self.serviceFails = 0
            DispatchQueue.main.async { self.applyMic(announce: true) }
            DispatchQueue.main.async { self.refreshMenu() }
        }
        link.onPCM = { [weak self] d in self?.audio.enqueue(d) }
        link.onJSON = { [weak self] m in DispatchQueue.main.async { self?.handle(m) } }

        HotKeys.shared.handler = { [weak self] name in DispatchQueue.main.async { self?.hotkey(name) } }
        // 按住说话：⌥⌘J 按住超过 0.6 秒再松开 = 说完了，马上出结果
        var talkDown = Date.distantPast
        var talkHeld = false
        HotKeys.shared.pressHandler = { [weak self] name, pressed in
            guard name == "talk" else { return }
            DispatchQueue.main.async {
                if pressed {
                    let t = Date()
                    talkDown = t
                    talkHeld = false
                    // 0.6 秒后还按着 → 按住说话模式：中间停顿不算说完，等松手
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
                        if talkDown == t {
                            talkHeld = true
                            self?.link.send(json: ["t": "hotkey", "name": "talk_hold"])
                        }
                    }
                } else {
                    talkDown = .distantPast
                    if talkHeld {
                        talkHeld = false
                        self?.link.send(json: ["t": "hotkey", "name": "talk_release"])
                    }
                }
            }
        }
        HotKeys.shared.register()

        // 外接键盘右侧修饰键：默认按住右 ⌘ 说话、轻按右 ⌥ 停（要「输入监控」权限）
        talkKey.log = { [weak self] s in self?.link.log(s) }
        talkKey.onDown = { [weak self] in
            guard let self = self else { return }
            if !self.listening { self.hotkey("talk") }
            self.link.send(json: ["t": "hotkey", "name": "talk_hold"])
        }
        talkKey.onUp = { [weak self] in self?.link.send(json: ["t": "hotkey", "name": "talk_release"]) }
        talkKey.onStop = { [weak self] in self?.hotkey("stop") }
        talkKey.start(TalkKey.Key(rawValue: UserDefaults.standard.string(forKey: "talkKey") ?? "rightCommand") ?? .rightCommand)

        setupMenu()
        captions.authorize()
        AVCaptureDevice.requestAccess(for: .audio) { ok in
            DispatchQueue.main.async {
                if !ok { self.link.log("麦克风权限没开（系统设置 → 隐私与安全性 → 麦克风 → DeckVoice）") }
                self.micAllowed = ok
                self.setMic(self.micOn, announce: false)
            }
        }
        link.start()
        Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in self?.superviseService() }
        Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in self?.checkOtherApps() }
        superviseService()
    }

    // ---------- 服务消息 ----------

    func handle(_ m: [String: Any]) {
        switch m["t"] as? String {
        case "listen":
            let on = m["on"] as? Bool ?? false
            if on && !listening {
                listening = true
                captions.start(hotwords: m["hotwords"] as? [String] ?? [])
            } else if !on && listening {
                listening = false
                captions.stop()
            }
        case "mic":
            setMic(m["on"] as? Bool ?? true, announce: true)
        case "status_request":
            applyMic(announce: true)
        case "stop":
            audio.stopPlayback()
        case "hold":
            audio.hold(m["on"] as? Bool ?? false)
        case "duck":
            audio.duck(m["on"] as? Bool ?? false)
        case "vp":
            audio.setSession(m["on"] as? Bool ?? false)
        case "play_mark":
            if let id = m["id"] { audio.mark(id) }
        default: break
        }
    }

    func hotkey(_ name: String) {
        if name == "mic" { setMic(!micOn, announce: true) }
        if name == "talk" && !micEffective { link.log("麦克风现在关着（\(micOn ? "让给了别的 App" : "已手动关闭")），⌥⌘J 没法听") }
        link.send(json: ["t": "hotkey", "name": name])
        if name == "stop" { audio.stopPlayback() }  // 不等服务，先让声音停
    }

    /// micOn 是他想不想开；真正开不开还要看权限和有没有让给别的 App
    var micEffective: Bool { micOn && micAllowed && yieldedTo.isEmpty }

    func setMic(_ on: Bool, announce: Bool) {
        micOn = on
        UserDefaults.standard.set(on, forKey: "micOn")
        applyMic(announce: announce)
    }

    func applyMic(announce: Bool) {
        let on = micEffective
        audio.setMic(on)
        if !on && listening { listening = false; captions.stop() }
        if announce || link.connected {
            var m: [String: Any] = ["t": "mic", "on": on]
            if micOn && !on { m["reason"] = !micAllowed ? "no_permission" : "other_app"; m["apps"] = yieldedTo }
            link.send(json: m)
        }
        refreshMenu()
    }

    var othersRecording = false
    func checkOtherApps() {
        // 不让麦时也要看：别的 App 在录音就临时关回声消除，否则它收到的声音会被系统压得很小（Claude 听写波形不动）
        if !yieldMic {
            let busy = micOn && micAllowed && !Devices.otherAppsUsingMic().isEmpty
            if busy != othersRecording { othersRecording = busy; audio.suspendVP(busy) }
        } else if othersRecording { othersRecording = false; audio.suspendVP(false) }
        guard yieldMic, micOn, micAllowed else {
            if !yieldedTo.isEmpty { yieldedTo = []; applyMic(announce: true) }
            return
        }
        let others = Devices.otherAppsUsingMic()
        if others.isEmpty == yieldedTo.isEmpty { return }
        if others.isEmpty {
            link.log("别的 App 不用麦克风了，接着听")
        } else {
            link.log("\(others.joined(separator: ", ")) 在用麦克风，先让出来")
        }
        yieldedTo = others
        applyMic(announce: true)
    }

    // ---------- 守护 Python 服务 ----------

    func superviseService() {
        if link.connected { return }
        if let p = service, p.isRunning { return }
        if Date() < nextServiceTry { return }
        let script = repo + "/manager/voice/service/run.sh"
        guard FileManager.default.fileExists(atPath: script) else {
            NSLog("[DeckVoice] 找不到 %@", script)
            nextServiceTry = Date().addingTimeInterval(30)
            return
        }
        let logDir = NSHomeDirectory() + "/Library/Logs"
        let logPath = logDir + "/DeckVoice-service.log"
        if !FileManager.default.fileExists(atPath: logPath) { FileManager.default.createFile(atPath: logPath, contents: nil) }
        let fh = FileHandle(forWritingAtPath: logPath)
        fh?.seekToEndOfFile()
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = [script]
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        p.environment = env
        if let fh = fh { p.standardOutput = fh; p.standardError = fh }
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                guard let self = self else { return }
                if proc.terminationStatus != 0 {
                    self.serviceFails += 1
                    let wait = min(60.0, pow(2.0, Double(self.serviceFails)))
                    self.nextServiceTry = Date().addingTimeInterval(wait)
                    NSLog("[DeckVoice] 语音服务退出（%d），%.0f 秒后重启", proc.terminationStatus, wait)
                } else {
                    self.nextServiceTry = Date().addingTimeInterval(10)  // 已有一份在跑
                }
                self.refreshMenu()
            }
        }
        do { try p.run(); service = p } catch { NSLog("[DeckVoice] 启动语音服务失败：%@", "\(error)") }
    }

    func applicationWillTerminate(_ n: Notification) {
        // 告诉语音服务这是正常退出，别自动把 DeckVoice 拉回来（服务若是别处起的、不跟着退出时有用）
        link.send(json: ["t": "quit"])
        Thread.sleep(forTimeInterval: 0.2)
        service?.terminate()
    }

    // ---------- 菜单栏 ----------

    let micItem = NSMenuItem(title: "", action: #selector(toggleMic), keyEquivalent: "")
    let serviceItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    let aecItem = NSMenuItem(title: "回声消除（说话时能被「停」打断）", action: #selector(toggleAEC), keyEquivalent: "")
    let yieldItem = NSMenuItem(title: "别的 App 录音时完全让出麦克风（默认只临时关回声消除）", action: #selector(toggleYield), keyEquivalent: "")

    func setupMenu() {
        status = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        let menu = NSMenu()
        micItem.target = self
        aecItem.target = self
        yieldItem.target = self
        menu.addItem(micItem)
        let talk = NSMenuItem(title: "直接说（按住右 ⌘ 或 ⌥⌘J，松手出结果）", action: #selector(talk), keyEquivalent: "")
        talk.target = self
        menu.addItem(talk)
        let stop = NSMenuItem(title: "停（轻按右 ⌥ 或 ⌥⌘.）", action: #selector(stopNow), keyEquivalent: "")
        stop.target = self
        menu.addItem(stop)
        let keyMenu = NSMenu()
        for (title, k) in [("右 ⌘ 说话 · 右 ⌥ 停（默认）", "rightCommand"), ("右 ⌥ 说话 · 右 ⌘ 停", "rightOption"), ("不用右侧按键", "none")] {
            let it = NSMenuItem(title: title, action: #selector(pickTalkKey(_:)), keyEquivalent: "")
            it.target = self
            it.representedObject = k
            keyMenu.addItem(it)
        }
        let keyItem = NSMenuItem(title: "按住说话的键", action: nil, keyEquivalent: "")
        keyItem.submenu = keyMenu
        menu.addItem(keyItem)
        talkKeyMenu = keyMenu
        menu.addItem(.separator())
        menu.addItem(aecItem)
        menu.addItem(yieldItem)
        menu.addItem(serviceItem)
        let logs = NSMenuItem(title: "打开语音服务日志", action: #selector(openLog), keyEquivalent: "")
        logs.target = self
        menu.addItem(logs)
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "退出 DeckVoice", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        status.menu = menu
        refreshMenu()
    }

    func refreshMenu() {
        guard status != nil else { return }
        let symbol = micEffective ? "waveform" : (micOn ? "mic.badge.xmark" : "mic.slash")
        let img = NSImage(systemSymbolName: symbol, accessibilityDescription: "DeckVoice")
            ?? NSImage(systemSymbolName: "mic.slash", accessibilityDescription: "DeckVoice")
        img?.isTemplate = true
        status.button?.image = img
        if !yieldedTo.isEmpty {
            micItem.title = "麦克风已让给 \(yieldedTo.first!)（关闭：⌥⌘M）"
        } else {
            micItem.title = micOn ? "关闭麦克风（⌥⌘M）" : "打开麦克风（⌥⌘M）"
        }
        aecItem.state = audio.aec ? .on : .off
        yieldItem.state = yieldMic ? .on : .off
        for it in talkKeyMenu?.items ?? [] { it.state = (it.representedObject as? String) == talkKey.key.rawValue ? .on : .off }
        serviceItem.title = link.connected ? "语音服务：运行中" : "语音服务：启动中…"
    }

    @objc func toggleMic() { setMic(!micOn, announce: true) }
    @objc func talk() { hotkey("talk") }
    @objc func stopNow() { hotkey("stop") }
    @objc func toggleAEC() {
        audio.aec.toggle()
        UserDefaults.standard.set(audio.aec, forKey: "aec")
        audio.restart()
        refreshMenu()
    }
    var talkKeyMenu: NSMenu?
    @objc func pickTalkKey(_ sender: NSMenuItem) {
        let k = sender.representedObject as? String ?? "none"
        UserDefaults.standard.set(k, forKey: "talkKey")
        talkKey.start(TalkKey.Key(rawValue: k) ?? .none)
        refreshMenu()
    }
    @objc func toggleYield() {
        yieldMic.toggle()
        UserDefaults.standard.set(yieldMic, forKey: "yieldMic")
        checkOtherApps()
        refreshMenu()
    }
    @objc func openLog() {
        NSWorkspace.shared.open(URL(fileURLWithPath: NSHomeDirectory() + "/Library/Logs/DeckVoice-service.log"))
    }
}

if CommandLine.arguments.contains("--vptest") {
    AVCaptureDevice.requestAccess(for: .audio) { _ in VPTest.run() }
    RunLoop.main.run()
}

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
