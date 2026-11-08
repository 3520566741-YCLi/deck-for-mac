# 更新记录 / Changelog

版本号规则：小修小补升第三位（0.1.0 → 0.1.1），加新功能升第二位（0.1.x → 0.2.0）。旧版本一直保留在 [Releases](../../releases)。
Versioning: fixes bump the patch number (0.1.0 → 0.1.1), new features bump the minor number (0.1.x → 0.2.0). Every old version stays on the [Releases](../../releases) page.

## v0.2.0 · 2026-11-08

新功能：键盘清洁；设置页切换显示方式。
New: keyboard cleaning; switch the display mode from Settings.

**新增 / Added**

- 工具箱新增「小工具」页（放不碰文件的随手工具），第一个是**键盘清洁**：锁住键盘，按什么都没反应，方便擦键盘；可选黑屏（灰尘指纹看得清）和连触控板一起锁。
  A new **Tools** tab in the toolbox for small utilities that never touch files. The first one is **keyboard cleaning**: the keyboard does nothing while you wipe it; optionally black out the screen (dust shows up better) and lock the trackpad too.
- 解锁方式：屏幕上按住按钮 1 秒、同时按住左右 ⌘ 2 秒、倒计时结束（默认 90 秒，最长 10 分钟）、Deck 里点解锁、对助手说「解锁键盘」；合盖、睡眠、锁屏时自动解锁。
  Unlock by holding the on-screen button for 1 s, holding both ⌘ keys for 2 s, waiting for the timer (90 s by default, 10 min max), tapping Unlock in Deck, or telling the assistant; it also unlocks on lid close, sleep and screen lock.
- 锁屏面板跟 Deck 的皮肤走：新拟态或液态玻璃（macOS 26+），白天 / 夜里都有。
  The lock panel follows Deck's skin: neumorphic or Liquid Glass (macOS 26+), day and night.
- 设置 → 显示方式：自动 / 扩展屏 / 刘海，和菜单栏是同一个设置。皮肤选项「玻璃（原来的）」改名为「玻璃」。
  Settings → Display: Auto / External screen / Notch, the same setting as the menu bar. The skin option "Glass (original)" is now just "Glass".

**怎么实现 / How it works**

- 拦按键的是 DeckHands 的清洁模式（`manager/hands/CleanMode.swift`，`--clean` 单独起进程），沿用 DeckHands 的辅助功能权限，不用另外授权。写法参考 MIT 开源的 [Lockpaw](https://github.com/sorkila/lockpaw) 和 [keylock](https://github.com/kfv/keylock)。更新后要重新跑一次 `manager/hands/build.sh`。
  Keys are blocked by DeckHands' clean mode (`manager/hands/CleanMode.swift`, a separate `--clean` process) using DeckHands' existing Accessibility permission. Approach based on the MIT-licensed Lockpaw and keylock. Re-run `manager/hands/build.sh` after updating.

**安全和稳定 / Safety and stability**

- 绝不锁死：进程卡住 6 秒（心跳停了）或过了截止时间，服务端直接结束它；进程一退出系统就撤掉拦截。结束后恢复被误按的 Caps Lock。
  It can't get stuck: if the process hangs for 6 s (no heartbeat) or runs past its deadline, the server ends it, and the system drops the block as soon as the process exits. Caps Lock is restored afterwards.
- 接口只认本机加口令；只会给确认过的 `DeckHands --clean` 进程发信号，不会误杀别的程序。
  The endpoints accept local requests with the token only, and signals go only to a verified `DeckHands --clean` process.
- 修复：工具箱里的报错之前显示成灰字，现在是醒目的提醒色。 / Fix: toolbox errors were shown in grey; they now use the warning color.

## v0.1.1 · 2026-11-08

修语音助手叫不醒、说了「我查一下」却不查。
Fixes the assistant not waking up, and saying "let me check" without actually checking.

**修复 / Fixes**

- 唤醒多一条路：唤醒词模型没反应时，把待机时的短句转成文字，句首是助手名字（前面可以有「你好 / Hey」）再核对声纹，也能叫醒。实测真人录音 26/26 叫醒，含相似发音的句子 0 次误唤醒。
  A second wake path: when the keyword model misses, short phrases heard while idle are transcribed; if the assistant's name starts the phrase (optionally after "hi / hey"), the voiceprint is checked and it wakes. In tests, 26/26 real recordings woke it and similar-sounding sentences caused 0 false wakes.
- 「你好 Vesper，现在几点」一口气说完能直接回答，不用再说一遍。 / "Hey Vesper, what time is it" in one breath now gets answered directly.
- 唤醒自检：每 10 分钟用你录入声纹时的录音测一遍，坏了在 Deck 上提醒；语音服务断开超过 1 分钟也提醒。 / Wake self-test every 10 minutes using your enrollment recording; Deck warns you if it fails or if the voice service is gone for over a minute.
- 助手不再说「我查一下再告诉你」然后没下文：要查就当场联网查完再答。 / The assistant no longer promises to "check and get back to you": it searches the web in the same turn.
- 接话时你的声音不会再被误当成别人。 / Your follow-up replies are no longer mistaken for someone else's voice.

**新增 / Added**

- DeckVoice 看门狗（可选）：登录时和每 5 分钟检查，语音小助手没在运行就后台打开，见 `manager/voice/helper/keepalive/`。 / Optional DeckVoice watchdog: at login and every 5 minutes it reopens the voice helper if it isn't running. See `manager/voice/helper/keepalive/`.

**安全 / Security**

- 唤醒自检接口 20 秒内最多真跑一次，防止别的网页反复触发占用 CPU。 / The wake self-test endpoint runs at most once per 20 seconds, so other web pages can't hammer the CPU.

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
