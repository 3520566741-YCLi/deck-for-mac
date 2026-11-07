import AppKit
import ServiceManagement

// 命令行开关（不开窗口，做完就退出）：
//   --register-login-item    打开开机自启（SMAppService，和菜单里的「开机时自动打开」同一个开关）
//   --unregister-login-item  关闭开机自启
//   --login-item-status      打印当前状态
let args = CommandLine.arguments
func statusText(_ s: SMAppService.Status) -> String {
    switch s {
    case .enabled: return "enabled"
    case .requiresApproval: return "requiresApproval（去 系统设置 → 通用 → 登录项 里允许）"
    case .notRegistered: return "notRegistered"
    case .notFound: return "notFound"
    @unknown default: return "unknown"
    }
}
if args.contains("--register-login-item") || args.contains("--unregister-login-item") || args.contains("--login-item-status") {
    var code: Int32 = 0
    do {
        if args.contains("--register-login-item") { try SMAppService.mainApp.register() }
        if args.contains("--unregister-login-item") { try SMAppService.mainApp.unregister() }
    } catch {
        print("失败：\(error.localizedDescription)")
        code = 1
    }
    print("登录项状态：\(statusText(SMAppService.mainApp.status))")
    exit(code)
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
