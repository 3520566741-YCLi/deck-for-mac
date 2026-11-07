import Foundation
import Network

/// 和 Python 语音服务的 TCP 通道（127.0.0.1:8779）。帧：1 字节类型 + 4 字节大端长度 + 负载。断线每秒重连。
final class Link {
    var onJSON: (([String: Any]) -> Void)?
    var onPCM: ((Data) -> Void)?
    var onConnected: (() -> Void)?
    private(set) var connected = false
    private let port: UInt16
    private var conn: NWConnection?
    private let q = DispatchQueue(label: "deckvoice.link")

    init(port: UInt16 = 8779) { self.port = port }

    func start() { q.async { self.connect() } }

    private func connect() {
        let c = NWConnection(host: "127.0.0.1", port: NWEndpoint.Port(rawValue: port)!, using: .tcp)
        conn = c
        c.stateUpdateHandler = { [weak self] st in
            guard let self = self else { return }
            switch st {
            case .ready:
                self.connected = true
                self.readHeader(c)
                self.onConnected?()
            case .failed, .cancelled:
                self.dropped(c)
            case .waiting:
                c.cancel()
            default: break
            }
        }
        c.start(queue: q)
    }

    private func dropped(_ c: NWConnection) {
        guard c === conn else { return }
        connected = false
        conn = nil
        q.asyncAfter(deadline: .now() + 1) { self.connect() }
    }

    private func readHeader(_ c: NWConnection) {
        c.receive(minimumIncompleteLength: 5, maximumLength: 5) { [weak self] data, _, done, err in
            guard let self = self else { return }
            guard let d = data, d.count == 5, err == nil else { c.cancel(); self.dropped(c); return }
            let kind = d[d.startIndex]
            let n = d.subdata(in: d.startIndex + 1 ..< d.startIndex + 5).reduce(0) { ($0 << 8) | Int($1) }
            if n == 0 { self.handle(kind, Data()); if !done { self.readHeader(c) }; return }
            self.readBody(c, kind: kind, n: n)
        }
    }

    private func readBody(_ c: NWConnection, kind: UInt8, n: Int) {
        c.receive(minimumIncompleteLength: n, maximumLength: n) { [weak self] data, _, _, err in
            guard let self = self else { return }
            guard let d = data, d.count == n, err == nil else { c.cancel(); self.dropped(c); return }
            self.handle(kind, d)
            self.readHeader(c)
        }
    }

    private func handle(_ kind: UInt8, _ d: Data) {
        switch kind {
        case UInt8(ascii: "J"):
            if let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any] { onJSON?(o) }
        case UInt8(ascii: "P"):
            onPCM?(d)
        default: break
        }
    }

    func send(kind: Character, _ payload: Data) {
        guard connected, let c = conn else { return }
        var frame = Data([kind.asciiValue!])
        var n = UInt32(payload.count).bigEndian
        frame.append(Data(bytes: &n, count: 4))
        frame.append(payload)
        c.send(content: frame, completion: .contentProcessed { _ in })
    }

    func send(json: [String: Any]) {
        if let d = try? JSONSerialization.data(withJSONObject: json) { send(kind: "J", d) }
    }

    func log(_ s: String) {
        NSLog("[DeckVoice] %@", s)
        send(json: ["t": "log", "text": s])
    }
}
