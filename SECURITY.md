# 安全 / Security

## 设计 / Design

- 所有服务只监听 `127.0.0.1`（8777 管家、8778 语音页面、8779 语音小助手）。可选的「iPad 访问」默认关闭，打开后局域网设备凭配对链接只能看、不能改。
  Every service listens on `127.0.0.1` only (8777 butler, 8778 voice page, 8779 voice helper). The optional "iPad access" is off by default; when on, LAN devices with the pairing link can view but not change anything.
- 写操作需要每次启动随机生成的令牌，且必须同源；`Host` 头不是 `127.0.0.1` / `localhost` 的请求一律拒绝（防 DNS 重绑定）。
  Writes need a per-launch random token and a same-origin request; any request whose `Host` is not `127.0.0.1` / `localhost` is rejected (DNS rebinding).
- API key 只放在 macOS 钥匙串，不写进仓库、配置、日志或页面。
  API keys live only in the macOS Keychain, never in the repo, config, logs or pages.
- 网页、屏幕、文件名、新闻里的文字只当数据，不当给助手的指令；删除、发送、付款、关应用等操作必须你点确认。
  Text from web pages, the screen, file names and news is treated as data, never as instructions to the assistant; deleting, sending, paying or quitting apps always needs your confirmation.
- 「删除」都只是移到废纸篓，可以放回；Deck 不会清空废纸篓。
  Every removal is a move to the Trash and can be undone; Deck never empties the Trash.
- 聊天记录、记忆、声纹只存在 `~/Library/Application Support/Deck/`，权限只给当前用户。
  Chat history, memories and voiceprints stay in `~/Library/Application Support/Deck/`, readable by the current user only.
- 第三方授权只要只读（长桥只要「自选」）。 / Third-party grants are read-only (Longbridge: watchlist only).

## 报告漏洞 / Reporting a vulnerability

请用 GitHub 的 [私下报告漏洞](../../security/advisories/new) 功能，不要开公开 issue。
Please use GitHub's [private vulnerability reporting](../../security/advisories/new) instead of a public issue.
