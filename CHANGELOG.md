# 更新记录 / Changelog

## v0.1.0 · 2026-11-07

第一版公开发布：放出完整代码，不再只是施工说明。
First public release with the complete code, not just a build guide.

**有什么 / What's in it**

- 控制台：Mac 实时状态、Claude Code / Codex 剩余额度、DeepSeek 余额、GitHub 仓库管家、我的应用、每日清理、工具箱；新拟态皮肤，白天 / 夜里，卡片可拖可改大小。
  Dashboard: live Mac status, Claude Code / Codex quota, DeepSeek balance, GitHub repo manager, my apps, daily cleanup, toolbox; neumorphic skin, day / night, draggable and resizable cards.
- 磁盘面板：谁在变大、最近新增、谁在写硬盘。 / Disk panel: what grows, what's new, who writes.
- 语音助手：本机唤醒、声纹、识别（Qwen3-ASR）、合成（Qwen3-TTS），大模型默认 DeepSeek；能打断、能接着说。
  Voice assistant: local wake word, voiceprint, recognition (Qwen3-ASR) and synthesis (Qwen3-TTS); DeepSeek by default; barge-in and follow-up.
- 资讯页：每天三期个人科技日报，自选股资讯以长桥证券为例（只读、只要自选权限）。
  News page: a personal tech paper three times a day; watchlist news with Longbridge as the example broker (read-only, watchlist scope only).
- 动手：DeckHands 用辅助功能 API 点按，风险操作先确认。 / Hands: clicks via the Accessibility API, risky steps confirmed first.
- 原生壳 Deck.app：扩展屏铺满，或从刘海弹出（⌥空格）。 / Native shell: fills an external screen or drops from the notch (⌥Space).
- 中英双语的 AI 安装说明和外部服务核查清单。 / Bilingual install guide for AI assistants and a service verification list.

**发布前的审查 / Reviews before release**

- 全功能实测：约 35 个接口逐个调用；10 分钟压测 296 次请求全部成功，内存、线程、文件句柄不增长。
  Full functional pass: about 35 endpoints called; 10-minute soak, 296/296 requests OK, no growth in memory, threads or file handles.
- 安全修复：语音服务加 Host 校验防 DNS 重绑定；写请求要求同源；非法 Content-Length 直接拒绝；空闲连接超时；记忆库和声纹只有本人可读。
  Security fixes: Host check on the voice service against DNS rebinding; same-origin writes; invalid Content-Length rejected; idle connection timeout; memory database and voiceprints readable by the owner only.
- 稳定性修复：空间统计不再被卡住的 `du` 拖住；语音日志带时间。
  Stability fixes: the disk-space scan no longer hangs on a stuck `du`; voice logs have timestamps.
- 隐私：代码由脚本自动脱敏后再扫描一遍，不含任何个人信息。
  Privacy: the code is scrubbed by script and scanned again; no personal data.
