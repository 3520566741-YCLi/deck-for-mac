import AudioToolbox
import AVFoundation
import CoreAudio
import Foundation

/// 唯一的音频引擎：麦克风（开回声消除）+ 播放 TTS。
/// 麦克风开着时用 voice processing，助手自己的声音会从麦克风信号里减掉，所以说话中也能听见「停」。
/// 麦克风关掉时整个输入都不碰（菜单栏不会亮橙点），只留播放。
final class AudioCore {
    var onMic16k: (([Float]) -> Void)?           // 16 kHz 单声道，约 100ms 一块
    var onNativeBuffer: ((AVAudioPCMBuffer) -> Void)?  // 原始采样率单声道，给 Apple 实时识别
    var onPlaybackMark: ((Any) -> Void)?
    var onAEC: ((Bool) -> Void)?                 // 回声消除实际开没开（服务端据此决定防回声的力度）
    private(set) var aecOn = false
    var log: ((String) -> Void)?
    var aec = UserDefaults.standard.object(forKey: "aec") as? Bool ?? true

    private var engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private var playerRate: Double = 0
    private var converter: AVAudioConverter?
    private var monoFormat: AVAudioFormat?
    private let out16k = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false)!
    private var pending: [Float] = []
    private var generation = 0       // 每次 stop 加一，旧的播放回调作废
    private(set) var micOn = false
    private var tapInstalled = false
    private let q = DispatchQueue(label: "deckvoice.audio")

    private var lastSignature = ""
    private var lastRebuild = Date.distantPast
    private var failures = 0
    private var vpRetries = 0
    private var vpEverWorked = false
    private var vpBroken = false
    private var vpSuspended = false  // 别的 App 在录音时临时关掉回声消除（开着时系统会把别的 App 收到的声音压得很小）
    private var vpSession = false    // 服务要求临时开（Vesper 在想/在说的时候），说完回到待机再关
    private var vpWanted: Bool { aec || vpSession }
    var aecActive: Bool { aec && !vpBroken }

    init() {
        // 设备插拔（比如 iPad 扩展屏接上/断开）后重新选设备。
        // 注意：开回声消除时系统会临时建一个聚合设备，也会触发「设备列表变了」，
        // 所以只有「该用哪个麦克风/扬声器」真的变了才重建，否则会死循环。
        var pending: DispatchWorkItem?
        Devices.watch { [weak self] in
            pending?.cancel()
            let w = DispatchWorkItem {
                self?.q.async {
                    guard let self = self else { return }
                    let sig = Devices.signature()
                    if sig != self.lastSignature {
                        self.log?("音频设备变了，重新选择")
                        self.rebuild()
                    }
                }
            }
            pending = w
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0, execute: w)
        }
        NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: nil, queue: nil) { [weak self] n in
            guard let self = self, (n.object as? AVAudioEngine) === self.engine else { return }
            self.q.asyncAfter(deadline: .now() + 0.5) {
                // 刚重建完的那一下也会收到这个通知，忽略
                guard Date().timeIntervalSince(self.lastRebuild) > 3 else { return }
                self.log?("音频配置变了，重建引擎")
                self.rebuild()
            }
        }
    }

    func setMic(_ on: Bool) {
        q.async {
            if on == self.micOn && self.engine.isRunning && self.failures == 0 { return }  // 没变化就别打断
            self.micOn = on
            self.failures = 0
            self.rebuild()
        }
    }

    /// 强制重建（比如切换回声消除）
    /// 别的 App 开始/停止录音：临时关/开回声消除，麦克风本身一直开着（两边同时用）
    func suspendVP(_ on: Bool) {
        q.async {
            guard on != self.vpSuspended else { return }
            self.vpSuspended = on
            guard self.micOn, self.vpWanted, !self.vpBroken else { return }
            self.log?(on ? "别的 App 在录音，暂时关掉回声消除" : "别的 App 不录了，回声消除恢复")
            self.rebuild()
        }
    }

    /// 只在 Vesper 想和说的时候开回声消除：这样她说话时能被人声打断，平时又不影响别的 App 用麦克风、不压低唤醒声纹分。
    /// 服务在进入「想」时发 on（还没开始出声，重建引擎不会切断播放），回到待机一会儿后发 off。
    func setSession(_ on: Bool) {
        q.async {
            guard on != self.vpSession else { return }
            let before = self.vpWanted
            self.vpSession = on
            guard self.vpWanted != before, self.micOn, !self.vpBroken, !self.vpSuspended else { return }
            self.rebuild()
        }
    }

    func restart() { q.async { self.failures = 0; self.vpBroken = false; self.rebuild() } }

    /// 按当前 micOn 重建引擎。依次尝试：麦克风+回声消除 → 麦克风 → 只播放，保证至少能出声。
    private func rebuild() {
        lastRebuild = Date()
        lastSignature = Devices.signature()
        var attempts: [(Bool, Bool)] = []
        if micOn {
            // 回声消除只能跟随系统默认麦克风：默认是蓝牙耳机 / iPad 这类时不开，改用 Mac 自带麦克风
            if vpWanted && !vpBroken && !vpSuspended && Devices.pick(input: true) == nil { attempts.append((true, true)) }
            attempts.append((true, false))
        }
        attempts.append((false, false))
        for (i, (mic, vp)) in attempts.enumerated() {
            if let err = start(mic: mic, vp: vp) {
                if vp && !vpEverWorked { vpBroken = true }  // 这台机器上开不了，本次运行不再试（菜单里重新勾选会再试一次）
                log?("音频引擎启动失败（\(mic ? (vp ? "麦克风+回声消除" : "麦克风") : "只播放")）：\(err)")
                if i == attempts.count - 1 { scheduleRetry() }
                continue
            }
            failures = 0
            aecOn = mic && vp
            onAEC?(aecOn)
            log?("音频：输入 \(mic ? Devices.describe(input: true) : "关")，输出 \(Devices.describe(input: false))\(vp ? "，回声消除开" : "")")
            if micOn && !mic { scheduleRetry() }  // 麦克风没打开，过一会儿再试
            if vp { vpRetries = 0; vpEverWorked = true }
            // 回声消除这次没开起来（刚从别的 App 手里接回麦克风时常见），过几秒再试，最多 3 次
            // 只在之前开成功过的情况下重试（开机就失败的说明这台机器/系统版本不支持，别反复折腾麦克风）
            if mic && !vp && vpWanted && !vpSuspended && vpEverWorked && vpRetries < 2 {
                vpRetries += 1
                q.asyncAfter(deadline: .now() + 5) { [weak self] in
                    guard let self = self, self.micOn, self.vpWanted else { return }
                    self.log?("再试一次开回声消除")
                    self.rebuild()
                }
            }
            return
        }
    }

    private func scheduleRetry() {
        failures += 1
        let wait = min(60.0, pow(2.0, Double(failures)))
        q.asyncAfter(deadline: .now() + wait) { [weak self] in
            guard let self = self, self.failures > 0 else { return }
            self.rebuild()
        }
    }

    /// 起一个新引擎；成功返回 nil，失败返回原因
    private func start(mic: Bool, vp: Bool) -> String? {
        engine.stop()
        if tapInstalled { engine.inputNode.removeTap(onBus: 0); tapInstalled = false }
        if player.engine != nil { engine.detach(player) }
        engine = AVAudioEngine()
        playerRate = 0
        converter = nil
        pending.removeAll()
        engine.attach(player)
        // 必须先把 player 接到 mainMixer 再开回声消除：反过来（先开 VP 再接）在这台 Mac 上一律 -10875（11-05 vptest D/H）
        connectPlayer(rate: 24000)
        if mic {
            let input = engine.inputNode
            if vp {
                do {
                    try input.setVoiceProcessingEnabled(true)
                    if #available(macOS 14.0, *) {
                        // 别把正在放的音乐压低
                        input.voiceProcessingOtherAudioDuckingConfiguration =
                            AVAudioVoiceProcessingOtherAudioDuckingConfiguration(enableAdvancedDucking: false, duckingLevel: .min)
                    }
                } catch { return "开回声消除失败 \(error.localizedDescription)" }
            } else if let dev = Devices.pick(input: true) {
                // 回声消除自己管设备（跟随系统默认）；不开时才手动指定，避开 iPad 这类虚拟设备
                setDevice(input.audioUnit, dev)
            }
            let fmt = input.outputFormat(forBus: 0)
            guard fmt.sampleRate > 0, fmt.channelCount > 0 else { return "没有可用的麦克风（格式 \(fmt)）" }
            let mono = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: fmt.sampleRate, channels: 1, interleaved: false)!
            monoFormat = mono
            converter = AVAudioConverter(from: mono, to: out16k)
            if let err = DVTry({
                input.installTap(onBus: 0, bufferSize: 1024, format: fmt) { [weak self] buf, _ in
                    self?.q.async { self?.process(buf) }
                }
            }) { return "接麦克风失败 \(err)" }
            tapInstalled = true
        }
        if !vp, let out = Devices.pick(input: false) { setDevice(engine.outputNode.audioUnit, out) }
        engine.prepare()
        do {
            try engine.start()
            if !held { safePlay() }
            return nil
        } catch {
            if tapInstalled { engine.inputNode.removeTap(onBus: 0); tapInstalled = false }
            return "\((error as NSError).code) \(error.localizedDescription)"
        }
    }

    private func setDevice(_ unit: AudioUnit?, _ dev: AudioDeviceID) {
        guard let u = unit else { return }
        var d = dev
        let st = AudioUnitSetProperty(u, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0, &d, UInt32(MemoryLayout<AudioDeviceID>.size))
        if st != noErr { log?("切换音频设备失败（\(st)），用系统默认") }
    }

    private func connectPlayer(rate: Double) {
        guard rate != playerRate else { return }
        let f = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, channels: 1, interleaved: false)!
        engine.disconnectNodeOutput(player)
        engine.connect(player, to: engine.mainMixerNode, format: f)
        playerRate = rate
    }

    // 麦克风：取第 1 声道 → 16 kHz
    private func process(_ buf: AVAudioPCMBuffer) {
        guard let mono = monoFormat, let conv = converter, let ch = buf.floatChannelData else { return }
        let n = Int(buf.frameLength)
        guard n > 0, let m = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: AVAudioFrameCount(n)) else { return }
        m.frameLength = AVAudioFrameCount(n)
        m.floatChannelData![0].update(from: ch[0], count: n)
        onNativeBuffer?(m)
        let cap = AVAudioFrameCount(Double(n) * 16000 / mono.sampleRate + 32)
        guard let o = AVAudioPCMBuffer(pcmFormat: out16k, frameCapacity: cap) else { return }
        var fed = false
        var err: NSError?
        conv.convert(to: o, error: &err) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true
            status.pointee = .haveData
            return m
        }
        let k = Int(o.frameLength)
        if k > 0 { pending.append(contentsOf: UnsafeBufferPointer(start: o.floatChannelData![0], count: k)) }
        while pending.count >= 1600 {
            onMic16k?(Array(pending.prefix(1600)))
            pending.removeFirst(1600)
        }
    }

    // ---------- 播放 ----------

    /// payload = 4 字节小端采样率 + float32 小端 PCM
    func enqueue(_ payload: Data) {
        guard payload.count > 4 else { return }
        q.async {
            let rate = Double(payload.prefix(4).withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) }.littleEndian)
            let samples = (payload.count - 4) / 4
            guard samples > 0, rate > 0 else { return }
            if rate != self.playerRate {
                self.connectPlayer(rate: rate)
                if !self.engine.isRunning { try? self.engine.start() }
                if !self.held { self.safePlay() }
            }
            let f = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, channels: 1, interleaved: false)!
            guard let b = AVAudioPCMBuffer(pcmFormat: f, frameCapacity: AVAudioFrameCount(samples)) else { return }
            b.frameLength = AVAudioFrameCount(samples)
            payload.dropFirst(4).withUnsafeBytes { raw in
                let src = raw.bindMemory(to: Float.self)
                b.floatChannelData![0].update(from: src.baseAddress!, count: samples)
            }
            if !self.engine.isRunning { try? self.engine.start() }
            self.player.scheduleBuffer(b, completionHandler: nil)
            if !self.player.isPlaying && !self.held { self.safePlay() }
        }
    }

    /// 播到这里时回调（TTS 用来知道说完了）
    func mark(_ id: Any) {
        q.async {
            let gen = self.generation
            let f = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: self.playerRate > 0 ? self.playerRate : 24000, channels: 1, interleaved: false)!
            guard let b = AVAudioPCMBuffer(pcmFormat: f, frameCapacity: 1) else { return }
            b.frameLength = 1
            self.player.scheduleBuffer(b, completionCallbackType: .dataPlayedBack) { [weak self] _ in
                guard let self = self else { return }
                self.q.async { if gen == self.generation { self.onPlaybackMark?(id) } }
            }
            if !self.player.isPlaying && !self.held { self.safePlay() }
        }
    }

    /// 引擎没起来时 play 会抛 NSException 让整个 DeckVoice 崩溃（11-07 03:56：蓝牙耳机连上又断开，
    /// 引擎启动失败后服务让它重开麦克风，重建时引擎还没起来就 play 了）。只在引擎真在跑时 play，并接住异常。
    private func safePlay() {
        guard engine.isRunning, player.engine === engine else {
            log?("引擎没在跑，先不播放（等下次重建）")
            return
        }
        if let err = DVTry({ self.player.play() }) {
            log?("播放启动失败：\(err)")
        }
    }

    /// 「停」：立刻清空播放队列
    func stopPlayback() {
        q.async {
            self.generation += 1
            self.holdGen += 1
            self.held = false
            self.player.stop()
            self.player.volume = 1
            self.safePlay()
        }
    }

    // ---------- 打断暂停 ----------
    // 他一开口先原地暂停（约 60ms 淡出再 pause，不「咔」一声，排队的块都留着）；
    // 服务判断不是真打断就淡入接着说，是真打断就发 stop 清空。
    private var held = false
    private var holdGen = 0

    func hold(_ on: Bool) {
        q.async {
            guard on != self.held else { return }
            self.held = on
            self.holdGen += 1
            let gen = self.holdGen
            let steps = 4
            if on {
                let v0 = self.player.volume  // 可能已经被 duck 压低了：从当前音量往下淡，别先跳回去
                for i in 1...steps {
                    self.q.asyncAfter(deadline: .now() + .milliseconds(15 * i)) {
                        guard gen == self.holdGen else { return }
                        self.player.volume = v0 * Float(steps - i) / Float(steps)
                        if i == steps { self.player.pause() }
                    }
                }
            } else {
                self.player.volume = 0
                if !self.engine.isRunning { try? self.engine.start() }
                self.safePlay()
                for i in 1...steps {
                    self.q.asyncAfter(deadline: .now() + .milliseconds(15 * i)) {
                        guard gen == self.holdGen else { return }
                        self.player.volume = Float(i) / Float(steps)
                    }
                }
            }
        }
    }

    // ---------- 让一下（duck） ----------
    // 他一开口（还没确认是不是他）先把她的音量压低，听起来就是「一开口她就让」；
    // 声纹确认是他 → hold 暂停；不是他 → 音量恢复，接着说。
    func duck(_ on: Bool) {
        q.async {
            guard !self.held else { return }
            self.holdGen += 1
            let gen = self.holdGen
            let v0 = self.player.volume, v1: Float = on ? 0.4 : 1.0
            let steps = 4
            for i in 1...steps {
                self.q.asyncAfter(deadline: .now() + .milliseconds(20 * i)) {
                    guard gen == self.holdGen, !self.held else { return }
                    self.player.volume = v0 + (v1 - v0) * Float(i) / Float(steps)
                }
            }
        }
    }
}
