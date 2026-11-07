import AVFoundation
import Foundation
import Speech

/// Apple 本机中文识别：只负责「边说边出字」的实时字幕。最终文字由 Python 那边的阿里模型定稿。
final class LiveCaptions {
    var onPartial: ((String) -> Void)?
    var onFinal: ((String) -> Void)?
    var log: ((String) -> Void)?

    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "zh-CN"))
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private let q = DispatchQueue(label: "deckvoice.captions")
    private(set) var authorized = false

    func authorize() {
        SFSpeechRecognizer.requestAuthorization { st in
            self.authorized = (st == .authorized)
            if !self.authorized { self.log?("语音识别权限没开（系统设置 → 隐私与安全性 → 语音识别），实时字幕先关闭，定稿不受影响") }
            if let r = self.recognizer, !r.supportsOnDeviceRecognition {
                self.log?("本机中文听写模型不可用，实时字幕会走 Apple 服务器")
            }
        }
    }

    func start(hotwords: [String]) {
        q.async {
            self.cancelLocked()
            guard self.authorized, let r = self.recognizer, r.isAvailable else { return }
            let req = SFSpeechAudioBufferRecognitionRequest()
            req.shouldReportPartialResults = true
            req.requiresOnDeviceRecognition = r.supportsOnDeviceRecognition  // 能本机就本机，不上传
            req.addsPunctuation = true
            req.taskHint = .dictation
            req.contextualStrings = Array(hotwords.prefix(100))
            self.request = req
            self.task = r.recognitionTask(with: req) { [weak self] result, error in
                guard let self = self else { return }
                if let res = result {
                    let t = res.bestTranscription.formattedString
                    if res.isFinal { self.onFinal?(t) } else { self.onPartial?(t) }
                }
                if error != nil || (result?.isFinal ?? false) {
                    self.q.async { if self.request === req { self.request = nil; self.task = nil } }
                }
            }
        }
    }

    func append(_ buf: AVAudioPCMBuffer) {
        q.async { self.request?.append(buf) }
    }

    func stop() {
        q.async {
            self.request?.endAudio()
            let t = self.task
            // 给它一点时间吐出最后的字，再收掉
            self.q.asyncAfter(deadline: .now() + 1.5) { if self.task === t { self.cancelLocked() } }
        }
    }

    private func cancelLocked() {
        task?.cancel()
        task = nil
        request = nil
    }
}
