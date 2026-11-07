import Foundation

/// 找到并拉起 Mac 管家的本地服务。
/// 查找顺序：设置里记住的路径（defaults write io.github.deckformac.deck butlerPath …）> ~/deck-for-mac/manager > 应用包内置的 manager。
final class ButlerService {
    enum Result { case ready(URL), failed(String) }

    var port: Int {
        let p = UserDefaults.standard.integer(forKey: "butlerPort")
        return p > 0 ? p : 8777
    }
    var baseURL: URL { URL(string: "http://127.0.0.1:\(port)/")! }
    var dashURL: URL { URL(string: "http://127.0.0.1:\(port)/dash?app=1")! }

    private var managerDir: String? {
        let fm = FileManager.default
        var candidates: [String] = []
        if let saved = UserDefaults.standard.string(forKey: "butlerPath") { candidates.append((saved as NSString).expandingTildeInPath) }
        candidates.append(NSHomeDirectory() + "/deck-for-mac/manager")
        if let res = Bundle.main.resourcePath { candidates.append(res + "/manager") }
        return candidates.first { fm.fileExists(atPath: $0 + "/server.py") }
    }

    func ensureRunning(_ done: @escaping (Result) -> Void) {
        healthy { [self] ok in
            if ok { return done(.ready(dashURL)) }
            guard let dir = managerDir else {
                return done(.failed("没找到 Mac 管家（~/deck-for-mac/manager/server.py）。先 clone 仓库到 ~/deck-for-mac，或在终端运行：defaults write io.github.deckformac.deck butlerPath <manager 文件夹路径>"))
            }
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            p.arguments = [dir + "/server.py", "--no-open"]
            p.currentDirectoryURL = URL(fileURLWithPath: dir)
            let log = dir + "/server.log"
            FileManager.default.createFile(atPath: log, contents: nil)
            if let h = FileHandle(forWritingAtPath: log) { p.standardOutput = h; p.standardError = h }
            do { try p.run() } catch {
                return done(.failed("Mac 管家启动失败：\(error.localizedDescription)。看看 \(log)。"))
            }
            // 服务会在后台一直跑（和从 Mac 管家.app 启动的是同一个），监控台退出也不停它
            self.waitUntilHealthy(tries: 20, log: log, done)
        }
    }

    private func waitUntilHealthy(tries: Int, log: String, _ done: @escaping (Result) -> Void) {
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.5) { [self] in
            healthy { ok in
                if ok { return done(.ready(self.dashURL)) }
                if tries <= 0 { return done(.failed("Mac 管家没有在 10 秒内启动。看看 \(log) 里的报错。")) }
                self.waitUntilHealthy(tries: tries - 1, log: log, done)
            }
        }
    }

    private func healthy(_ cb: @escaping (Bool) -> Void) {
        var req = URLRequest(url: baseURL.appendingPathComponent("api/health"))
        req.timeoutInterval = 2
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            cb((resp as? HTTPURLResponse)?.statusCode == 200)
        }.resume()
    }
}
