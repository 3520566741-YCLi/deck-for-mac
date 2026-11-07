import AVFoundation
import AudioToolbox
import Foundation

/// 诊断回声消除（voice processing）为什么起不来：open -n ~/Applications/DeckVoice.app --args --vptest
/// 结果写到 ~/Library/Logs/DeckVoice-vptest.log，跑完自己退出。
enum VPTest {
    static let path = NSHomeDirectory() + "/Library/Logs/DeckVoice-vptest.log"
    static func vlog(_ s: String) {
        let line = s + "\n"
        if let h = FileHandle(forWritingAtPath: path) { h.seekToEndOfFile(); h.write(line.data(using: .utf8)!); h.closeFile() }
        else { try? line.write(toFile: path, atomically: true, encoding: .utf8) }
    }
    static func run() {
        vlog("=== \(Date()) ===")

        func attempt(_ name: String, _ body: (AVAudioEngine) throws -> Void) {
            let e = AVAudioEngine()
            do {
                try body(e)
                e.prepare()
                try e.start()
                vlog("OK   \(name)  in=\(e.inputNode.outputFormat(forBus: 0)) out=\(e.outputNode.outputFormat(forBus: 0))")
                Thread.sleep(forTimeInterval: 0.5)
                e.stop()
            } catch {
                vlog("FAIL \(name): \((error as NSError).code) \(error.localizedDescription)")
            }
            Thread.sleep(forTimeInterval: 0.8)
        }

        attempt("A 只开 VP，不接任何节点") { e in try e.inputNode.setVoiceProcessingEnabled(true) }
        attempt("B VP + 输入 tap") { e in
            try e.inputNode.setVoiceProcessingEnabled(true)
            e.inputNode.installTap(onBus: 0, bufferSize: 1024, format: e.inputNode.outputFormat(forBus: 0)) { _, _ in }
        }
        attempt("C 先碰 mainMixer/outputNode，再开 VP + tap") { e in
            _ = e.mainMixerNode; _ = e.outputNode
            try e.inputNode.setVoiceProcessingEnabled(true)
            e.inputNode.installTap(onBus: 0, bufferSize: 1024, format: e.inputNode.outputFormat(forBus: 0)) { _, _ in }
        }
        attempt("D VP + tap + player(24k mono)→mainMixer") { e in
            try e.inputNode.setVoiceProcessingEnabled(true)
            e.inputNode.installTap(onBus: 0, bufferSize: 1024, format: e.inputNode.outputFormat(forBus: 0)) { _, _ in }
            let p = AVAudioPlayerNode(); e.attach(p)
            e.connect(p, to: e.mainMixerNode, format: AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 24000, channels: 1, interleaved: false))
        }
        attempt("E 同 D，player 用 mainMixer 的格式") { e in
            try e.inputNode.setVoiceProcessingEnabled(true)
            e.inputNode.installTap(onBus: 0, bufferSize: 1024, format: e.inputNode.outputFormat(forBus: 0)) { _, _ in }
            let p = AVAudioPlayerNode(); e.attach(p)
            e.connect(p, to: e.mainMixerNode, format: nil)
        }
        attempt("F VP 同时在 outputNode 上开 + D") { e in
            try e.outputNode.setVoiceProcessingEnabled(true)
            try e.inputNode.setVoiceProcessingEnabled(true)
            e.inputNode.installTap(onBus: 0, bufferSize: 1024, format: e.inputNode.outputFormat(forBus: 0)) { _, _ in }
            let p = AVAudioPlayerNode(); e.attach(p)
            e.connect(p, to: e.mainMixerNode, format: AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 24000, channels: 1, interleaved: false))
        }
        attempt("G 输入接到 mainMixer（音量 0）而不是 tap") { e in
            try e.inputNode.setVoiceProcessingEnabled(true)
            e.connect(e.inputNode, to: e.mainMixerNode, format: e.inputNode.outputFormat(forBus: 0))
            e.inputNode.volume = 0
        }
        let mono24 = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 24000, channels: 1, interleaved: false)
        func tap(_ e: AVAudioEngine) { e.inputNode.installTap(onBus: 0, bufferSize: 1024, format: e.inputNode.outputFormat(forBus: 0)) { _, _ in } }
        attempt("H 先接好 player→mainMixer，再开 VP + tap") { e in
            let p = AVAudioPlayerNode(); e.attach(p)
            e.connect(p, to: e.mainMixerNode, format: mono24)
            try e.inputNode.setVoiceProcessingEnabled(true)
            tap(e)
        }
        attempt("I VP + tap + player 直接→outputNode（outputNode 的格式）") { e in
            try e.inputNode.setVoiceProcessingEnabled(true)
            tap(e)
            let p = AVAudioPlayerNode(); e.attach(p)
            e.connect(p, to: e.outputNode, format: e.outputNode.inputFormat(forBus: 0))
        }
        attempt("J VP + tap + player(48k 单声道)→outputNode") { e in
            try e.inputNode.setVoiceProcessingEnabled(true)
            tap(e)
            let p = AVAudioPlayerNode(); e.attach(p)
            e.connect(p, to: e.outputNode, format: AVAudioFormat(standardFormatWithSampleRate: 48000, channels: 1))
        }
        attempt("K VP + tap 先启动，运行中再接 player→mainMixer") { e in
            try e.inputNode.setVoiceProcessingEnabled(true)
            tap(e)
            e.prepare()
            try e.start()
            let p = AVAudioPlayerNode(); e.attach(p)
            e.connect(p, to: e.mainMixerNode, format: mono24)
            p.play()
            e.stop()
        }
        // L：录音（VP）和播放分成两个引擎
        do {
            let rec = AVAudioEngine(), play = AVAudioEngine()
            try rec.inputNode.setVoiceProcessingEnabled(true)
            tap(rec)
            rec.prepare(); try rec.start()
            let p = AVAudioPlayerNode(); play.attach(p)
            play.connect(p, to: play.mainMixerNode, format: mono24)
            play.prepare(); try play.start()
            p.play()
            vlog("OK   L 两个引擎：VP 录音 + 普通播放")
            Thread.sleep(forTimeInterval: 0.5)
            play.stop(); rec.stop()
        } catch {
            vlog("FAIL L 两个引擎: \((error as NSError).code) \(error.localizedDescription)")
        }
        Thread.sleep(forTimeInterval: 0.8)
        // M：直接用 VoiceProcessingIO AudioUnit（不经过 AVAudioEngine），只看能不能初始化
        do {
            var desc = AudioComponentDescription(componentType: kAudioUnitType_Output, componentSubType: kAudioUnitSubType_VoiceProcessingIO,
                                                 componentManufacturer: kAudioUnitManufacturer_Apple, componentFlags: 0, componentFlagsMask: 0)
            if let comp = AudioComponentFindNext(nil, &desc) {
                var unit: AudioUnit?
                var st = AudioComponentInstanceNew(comp, &unit)
                if st == noErr, let u = unit {
                    var one: UInt32 = 1
                    st = AudioUnitSetProperty(u, kAudioOutputUnitProperty_EnableIO, kAudioUnitScope_Input, 1, &one, 4)
                    var fmt = AudioStreamBasicDescription(mSampleRate: 24000, mFormatID: kAudioFormatLinearPCM,
                                                          mFormatFlags: kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked | kAudioFormatFlagIsNonInterleaved,
                                                          mBytesPerPacket: 4, mFramesPerPacket: 1, mBytesPerFrame: 4, mChannelsPerFrame: 1, mBitsPerChannel: 32, mReserved: 0)
                    let s1 = AudioUnitSetProperty(u, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Input, 0, &fmt, UInt32(MemoryLayout.size(ofValue: fmt)))
                    let s2 = AudioUnitSetProperty(u, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Output, 1, &fmt, UInt32(MemoryLayout.size(ofValue: fmt)))
                    let s3 = AudioUnitInitialize(u)
                    let s4 = s3 == noErr ? AudioOutputUnitStart(u) : -1
                    vlog("\(s3 == noErr && s4 == noErr ? "OK  " : "FAIL") M 直接用 VoiceProcessingIO：enableIO=\(st) fmtIn=\(s1) fmtOut=\(s2) init=\(s3) start=\(s4)")
                    Thread.sleep(forTimeInterval: 0.5)
                    AudioOutputUnitStop(u); AudioUnitUninitialize(u); AudioComponentInstanceDispose(u)
                } else { vlog("FAIL M 创建 VoiceProcessingIO: \(st)") }
            } else { vlog("FAIL M 找不到 VoiceProcessingIO") }
        }
        vlog("default in: " + (AVCaptureDevice.default(for: .audio)?.localizedName ?? "?"))
        exit(0)
    }
}
