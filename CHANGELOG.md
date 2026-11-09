# 更新记录 / Changelog

版本号规则：小修小补升第三位（0.1.0 → 0.1.1），加新功能升第二位（0.1.x → 0.2.0）。旧版本一直保留在 [Releases](../../releases)。
Versioning: fixes bump the patch number (0.1.0 → 0.1.1), new features bump the minor number (0.1.x → 0.2.0). Every old version stays on the [Releases](../../releases) page.

## v0.4.0 · 2026-11-09

Agent 进度：Claude Code、Codex、DeepSeek Harness 干到哪了，直接看卡片。这一版只更新 Mac；Windows 版停在 v0.3.0。
Agent progress: see how far Claude Code, Codex and DeepSeek Harness have got, right on their cards. This release is Mac only; the Windows version stays at v0.3.0.

### 新增 / Added

- **Agent 进度**（做法借鉴开源项目 [Codex Pacer](https://github.com/RyanZhangNTU/codex-pacer)，MIT）：设置 → Agent 进度里逐个打开，Deck 会给这个 Agent 登记一个只在本机运行的小工具 `report_progress`，Agent 干活时顺手报「做到百分之几、在做哪一步」。进度环嵌在 Claude / Codex / DeepSeek 卡里，点开能看每一步。关掉开关会立即失效，并且只撤掉 Deck 自己加的那一项。
  **Agent progress** (approach borrowed from the open-source [Codex Pacer](https://github.com/RyanZhangNTU/codex-pacer), MIT): turn it on per agent in Settings → Agent progress. Deck registers a local-only tool, `report_progress`, so the agent reports "how far, which step" as it works. A progress ring sits inside the Claude / Codex / DeepSeek cards; tap to see every step. Turning it off takes effect at once and removes only what Deck added.
- **也看会话日志**：开关打开前就在跑的 Codex / Claude Code 任务，从它们自己写在本机的日志里读出来，只读、不改任何东西。这种任务没有百分比（除非 Agent 列了计划清单）。
  **Also read session logs**: Codex / Claude Code tasks that started before you turned the switch on are read from the logs they already write on your Mac. Read-only, nothing is changed. These tasks have no percent unless the agent made a plan checklist.
- **助手自动整理**：DeepSeek 判断 Agent 的原话能不能直接看，不合适才改写成好懂的话（中英各一份），并写两三句「干到哪了、顺不顺」。只在你开着 Deck 时整理，有频率和每日上限。
  **Automatic tidy-up**: DeepSeek decides whether the agent's own words read fine; only if not does it rewrite them plainly (in Chinese and English), and it adds two or three sentences on how far it is and how it's going. It only runs while Deck is open, with rate and daily limits.
- 放不下的文字会慢慢来回滚动，鼠标放上去停下并显示全文；系统开了「减弱动态效果」时只截断。
  Text that doesn't fit scrolls slowly back and forth; hover to pause and see all of it. With Reduce Motion on, it's just truncated.

### 改动 / Changed

- 刘海模式弹出更顺：动画期间不拉数据，也不做整块模糊。
  Notch mode opens more smoothly: no data fetching or full-panel blur during the animation.
- Claude / Codex 的用量额度改读官方数据，显示更准，助手读到的也一样。
  Claude / Codex usage limits now come from the official sources, so they're more accurate, for the assistant too.
- 英文模式补了一批漏翻。
  More strings translated in English mode.

### 修复 / Fixed

- 耳机断开后，语音助手不再一直占着麦克风、反复重试，不会再拖累别的软件用麦克风。
  After headphones disconnect, the voice helper no longer holds the microphone and retries in a loop, so other apps can use the mic again.

### 更新 / Updating

- **Mac**：拉取新代码后重启 Deck（退出 Deck → 结束 8777 上的 server.py → 重新打开），见 [AI_SETUP.md 第 10 节](AI_SETUP.md#10-更新到新版本)。
  **Mac**: pull the new code and restart Deck (quit Deck → stop server.py on port 8777 → reopen); see section 10 of [AI_SETUP.en.md](AI_SETUP.en.md).
- **Windows**：这一版没有 Windows 更新，继续用 v0.3.0 的安装包。
  **Windows**: no Windows update in this release; keep using the v0.3.0 installer.

## v0.3.0 · 2026-11-08

Deck 有 Windows 版了（带安装包），还有英文模式。
Deck now runs on Windows (with an installer), and has an English mode.

### 新增 / Added

- **Windows 版**：只有新拟态皮肤，Release 里有安装包 `Deck-Setup-0.3.0.exe`，装在用户文件夹、不要管理员权限。说明见 [docs/windows.md](docs/windows.md)。
  **Windows version**: neumorphic skin only, with an installer `Deck-Setup-0.3.0.exe` on the Release; installs into your user folder, no admin rights. See [docs/windows.en.md](docs/windows.en.md).
  - 主页、管家页、进程、磁盘面板、工具箱、每日清理、键盘清洁、资讯页、助手对话都能用，数据和任务管理器同源；所有「删除」只进回收站，能一键放回。
    Home, Butler, processes, disk panel, toolbox, daily cleanup, keyboard cleaning, news and the assistant all work, with data from the same source as Task Manager; every "delete" only goes to the Recycle Bin and can be put back.
  - **顶部面板**（刘海模式的 Windows 版）：鼠标碰屏幕顶边正中弹出，移开收起，全屏游戏和视频时不弹；托盘里切换 自动 / 窗口 / 顶部面板。
    **Top panel** (the Windows take on notch mode): touch the middle of the top edge to open it, move away to close; never pops over full-screen games or video. Switch Auto / Window / Top panel from the tray.
  - **语音**：唤醒、识别、朗读都在本机；Ctrl+Alt+M 开关麦克风，按住 Ctrl+Alt+J 说话。模型第一次启动自动下载并校验。
    **Voice**: wake word, recognition and speech all run locally; Ctrl+Alt+M toggles the mic, hold Ctrl+Alt+J to talk. Models download and are verified on first start.
  - **让助手操作电脑**：默认关；Ctrl+Alt+Esc 立刻停；不碰密码框、管理员窗口和凭据管理器，危险操作先问。
    **Let the assistant use the PC**: off by default; Ctrl+Alt+Esc stops it at once; never touches password boxes, admin windows or Credential Manager, and asks before anything risky.
  - API key 存进 Windows 凭据管理器（`cmdkey /generic:deepseek-api /user:deck /pass`），不写进文件。
    API keys go into Windows Credential Manager (`cmdkey /generic:deepseek-api /user:deck /pass`), never into a file.
  - 每次改动都在 GitHub 的 Windows 云电脑上自动测试、截图、打包（[windows.yml](.github/workflows/windows.yml)）。
    Every change is tested, screenshotted and packaged on a GitHub Windows cloud machine ([windows.yml](.github/workflows/windows.yml)).
- **英文模式**：设置 → 外观 → 语言（跟随系统 / 中文 / English），也可以让助手切。监控台、管家页、资讯页都翻译了；聊天原文和日报正文保持原样。
  **English mode**: Settings → Appearance → Language (Follow system / 中文 / English), or ask the assistant. Dashboard, Butler and News are translated; chat messages and the brief's text stay as written.

### 改动 / Changed

- 助手收起时的提示音换成和开启音相配的「从高往低」版本。
  The assistant's closing chime is now a falling version of its opening chime.

### 修复 / Fixed

- 第一次打开就关掉了几张卡时，主页会留空洞：现在会自动排紧。
  Turning off cards before the layout was ever saved left gaps on the home page; it now packs the cards.

### 更新 / Updating

- **Mac**：拉取新代码后重启 Deck（退出 Deck → 结束 8777 上的 server.py → 重新打开），见 [AI_SETUP.md 第 10 节](AI_SETUP.md#10-更新到新版本)。
  **Mac**: pull the new code and restart Deck (quit Deck → stop server.py on port 8777 → reopen); see section 10 of [AI_SETUP.en.md](AI_SETUP.en.md).
- **Windows**：下载 Release 里的 `Deck-Setup-0.3.0.exe` 安装。
  **Windows**: download and run `Deck-Setup-0.3.0.exe` from the Release.

## v0.2.1 · 2026-11-08

### 修复 / Fixed

- 日报里的股价不会再写错：编稿时把长桥自选的实时行情交给编辑，规定现价只能用行情里的数字；编完逐句核对口播和各栏标题摘要，和行情差 3% 以上的股价所在的句子自动删掉（目标价、市值、营收、产品售价不受影响）。拿不到行情时不写具体股价。
  Stock prices in the daily brief are now checked: the editor gets your Longbridge watchlist's live quotes and may only use those for current prices; afterwards every sentence in the script and headlines is checked, and any stock price more than 3% off the quotes is removed (target prices, market caps, revenue and product prices are left alone). Without quotes, no specific prices are written.
- 重启 Deck 时，还在念的日报会停下来。以前语音服务会接着念，新页面不知道，再按播放就叠成两遍。
  Restarting Deck now stops a brief that was still being read. Before, the voice service kept reading, the new page didn't know, and pressing play doubled it.
- 服务端日志改成按行写入，不会再卡在缓冲里或在进程退出时丢失。
  Server logs are now line-buffered, so they no longer sit in a buffer or get lost when the process exits.
- 助手强制调用工具被模型接口拒绝时会记一行日志。
  The assistant now logs when the model API rejects a forced tool call.

### 更新 / Updating

拉取新代码后重启 Deck（退出 Deck → 结束 8777 上的 server.py → 重新打开）。下一期日报开始生效。
Pull the new code and restart Deck (quit Deck → stop server.py on port 8777 → reopen). Takes effect from the next issue of the brief.

## v0.2.0 · 2026-11-08

这一版合并了原先分开准备的 0.2.0 和 0.3.0（都没正式发布过），一起发布：助手能读日报、能操作 Deck 的全部功能；每个按钮都有按压反馈和音效；键盘清洁；助手名字和声纹在 Deck 里自己设；设置页切换显示方式。
This release folds the separately prepared 0.2.0 and 0.3.0 (neither was published) into one: the assistant can read the daily brief and operate everything in Deck; every button gives press feedback and sound; keyboard cleaning; name the assistant and record your voiceprint inside Deck; switch the display mode from Settings.

### 助手能读日报、能操作 Deck 的全部功能 / The assistant can read the brief and operate all of Deck

#### 新增 / Added

- 说「读一下今天的日报」：资讯页的日报播放器用助手的声音从头念，逐句高亮。助手先短短应一声，等它说完才开始播，两段声音不会叠在一起。「暂停」「继续」「从头读」「下一条」都能用嘴说。
  Say "read today's brief": the news page's player reads it from the top in the assistant's voice, highlighting each sentence. The assistant answers in a few words first and the brief starts only after it finishes, so the two never overlap. "Pause", "continue", "from the top" and "next" work by voice.
- 资讯各栏都能问：头版、前沿科技、开源热榜、AI 巨头、艺术 × 代码、投资新闻，还有整篇口播稿。助手知道你现在翻到了哪一栏。
  Ask about any section: front page, frontier tech, trending open source, AI giants, art × code, investing news, or the whole script. The assistant knows which section you're looking at.
- 行情：「我的自选今天怎么样」「英伟达现在多少钱」，读的是资讯页已经拿到的只读快照（涨跌幅、盘前盘后、指数、市场温度），不能也不会下单。
  Quotes: "how's my watchlist today", "what's NVIDIA at": read from the news page's read-only snapshot (change, pre/after hours, indices, market temperature). It cannot and will not trade.
- 进程、磁盘、GitHub：谁最占 CPU / 内存 / GPU，磁盘剩多少、谁在变大、最近新增、谁在写硬盘，某个仓库最近的提交、PR、Actions 和访客来源。
  Processes, disk, GitHub: top CPU / memory / GPU users; free space, what's growing, what's new, who's writing; a repo's recent commits, PRs, Actions runs and referrers.
- 应用和工具箱：打开 / 退出 / 重启应用、在访达里显示、卸载连残留；扫空间分布、垃圾、大文件和重复文件、应用更新，并能清垃圾。
  Apps and toolbox: open / quit / restart apps, reveal in Finder, uninstall with leftovers; scan space, junk, large and duplicate files, and app updates, and clean junk.
- Deck 设置和显示方式：皮肤、排版、刘海面板大小和排法、玻璃透明度、按键音效、减少动画、显示或隐藏某张卡片；切换 自动 / 扩展屏 / 刘海。
  Deck settings and display: skin, layout, notch size and arrangement, glass opacity, key sounds, reduce motion, show or hide a card; switch Auto / External / Notch.
- 每日清理：问最近清了什么、开关「直接清」、一句话把清掉的东西放回原处。
  Daily cleanup: ask what was cleaned, turn auto-clean on or off, and put things back with one sentence.

#### 安全和稳定 / Safety and stability

- 会改动东西的操作（结束进程、退出或重启应用、卸载、清垃圾）一律先在屏幕上出「等你确认」，只有你最新一句话是简短明确的同意才执行；东西只进废纸篓，能撤销。网页、新闻、进程名里的文字只当数据，就算写着「帮我删掉」也不照做。
  Anything that changes your Mac (ending a process, quitting or restarting an app, uninstalling, cleaning junk) shows a confirmation first and runs only if your latest sentence is a short, clear yes. Things only go to the Trash and can be put back. Text from web pages, news or process names is treated as data, never as instructions.
- 系统进程和 Deck 自己要用的进程不能结束，系统应用和 Deck 自己的应用不能卸载。
  System processes and the ones Deck needs can't be ended; system apps and Deck's own apps can't be uninstalled.
- 助手只有真的调用了对应的功能才会说「已经暂停 / 开始播 / 退出了」：像命令的短句第一步必须执行操作，没做就说做了会当场补做。
  The assistant only says "paused / playing / quit" after actually doing it: short commands must run an action first, and a claim without an action is caught and carried out on the spot.
- 日报播放：连点、拖进度条、方向键不会让它反复重开；语音说「读日报」时不会被助手的追问窗口卡住。
  Brief player: rapid taps, dragging the seek bar or arrow keys no longer restart it over and over; saying "read the brief" is no longer blocked by the assistant's follow-up listening.
- 系统自带的应用（备忘录、计算器……）也能用中文名打开和退出。
  Built-in apps (Notes, Calculator…) can be opened and quit by their localized names.
- 修复：Deck 重新排版时（改皮肤、隐藏卡片）对话面板会被关掉。
  Fix: the chat panel closed whenever Deck re-laid out the page (changing skin, hiding a card).

### 每个按钮都有按压反馈和音效 / Every button gives press feedback and sound

- 资讯页、管家页、玻璃对话页之前没加载按键音效，日报播放器、「从头再念」这些按钮按了没声音、看起来也没反应；现在 Deck 里所有按钮都有按下缩放 + 回弹和按键音效（音效沿用原来那套，夜里自动减半、助手在听 / 在说时不响）。
  The news page, the butler page and the glass chat page never loaded the key sounds, so buttons like the brief player's play and "read again" made no sound and looked dead. Every button in Deck now has the press-and-spring feedback and the key sound (same sound set; half volume at night, silent while the assistant listens or speaks).
- 手指快点（不到 0.1 秒）也会显示按下的样子。 / Quick taps under 0.1 s now show the pressed state too.
- 「重编这一期」点了会转圈，编好之前不能重复点。 / "Re-edit this issue" shows a spinner and can't be pressed twice while it runs.
- 日报播放器防连点：连按跳句只重开一次，拖完进度条后方向键不会误跳句。 / The brief player ignores double taps: rapid seeking restarts only once, and arrow keys no longer seek after you drag the bar.

### 助手名字和声纹 / Assistant name and voiceprint

#### 新增 / Added

- 设置 →「助手」→「名字」：英文 3–12 个字母或 2–4 个汉字，可填读法提示（中文谐音或英文单词）。保存后唤醒当场换，不用重启；页面上的名字和大模型人设自动跟着变。Siri、Alexa、小爱这类别的助手的名字不收。
  Settings → Assistant → Name: 3–12 English letters or 2–4 Chinese characters, with an optional pronunciation hint. Wake switches immediately without a restart; the name on the page and the LLM persona follow. Names of other assistants (Siri, Alexa…) are refused.
- 「名字体检」：用 macOS 合成声音把各种叫法说一遍、再混进 28 句普通聊天，看两条唤醒路各认出几成、会不会误认。
  "Check this name": macOS synthetic voices say every wake phrase plus 28 ordinary sentences, showing how often each wake path catches the name and whether it misfires.
- 「声纹」：原来 8778/enroll 的引导录入搬进 Deck（新拟态和液态玻璃两套皮肤），录好一遍自动跳下一步；中文名字没有英文那几步。
  Voiceprint: the guided recording from 8778/enroll now lives in Deck (neumorphic and Liquid Glass skins) and moves to the next step automatically; Chinese names skip the English steps.
- 「测试」：自己叫 10 遍（唤醒率 + 声纹通过率）、请别人叫 10 遍（会不会被冒充）、安静听 1 / 3 / 5 分钟（会不会误醒）。
  Tests: say it 10 times (wake rate and voiceprint pass rate), have someone else say it 10 times (impersonation), or listen quietly for 1 / 3 / 5 minutes (false wakes).

#### 怎么实现 / How it works

- 读法：先查唤醒模型自带的 CMU 词典；查不到的名字按简化的字母发音规则（思路来自公有领域的 NRL 规则）猜几种读法；读法提示排最前（`manager/voice/service/names.py`）。
  Pronunciation: the wake model's CMU lexicon first; unknown names get a few guesses from simplified letter-to-sound rules (after the public-domain NRL rules); the hint goes first.
- 第二条唤醒路认名字：名字和识别结果都换成「粗音标」再比相似度，常用英文词要几乎一样才算；录声纹时识别器把名字写成什么会记下来。
  The second wake path compares a coarse phonetic key of the name and the transcript; common English words must match almost exactly; spellings the recognizer produced during enrollment are remembered.
- 记下的写法只收读音和名字像的（相似度 ≥ 0.6），会让普通聊天误醒的不收，免得名字被认成「太阳」之类常用词后一说就醒。
  Remembered spellings are kept only if they sound like the name (similarity ≥ 0.6) and don't trigger on ordinary sentences, so a name misheard as a common word won't cause false wakes.

#### 安全和稳定 / Safety and stability

- 测试时结果只交给测试、不真的唤醒，也绝不拿测试时的声音补录声纹（可能是别人）；测试最长 12 分钟自动结束。
  Tests never actually wake the assistant and never adapt the voiceprint (it might be someone else); a test ends after 12 minutes at most.
- 录入期间唤醒暂停，网页关了没退出时 15 分钟自动收尾；改名失败自动退回原名。
  Wake is paused while enrolling; if the page is closed mid-way it cleans up after 15 minutes. A failed rename falls back to the old name.
- 英文词典按需查，不整份读进内存（省约 60 MB）。
  The English lexicon is looked up on demand instead of loaded whole (saves about 60 MB).

### 键盘清洁和显示方式 / Keyboard cleaning and display mode

#### 新增 / Added

- 工具箱新增「小工具」页（放不碰文件的随手工具），第一个是**键盘清洁**：锁住键盘，按什么都没反应，方便擦键盘；可选黑屏（灰尘指纹看得清）和连触控板一起锁。
  A new **Tools** tab in the toolbox for small utilities that never touch files. The first one is **keyboard cleaning**: the keyboard does nothing while you wipe it; optionally black out the screen (dust shows up better) and lock the trackpad too.
- 解锁方式：屏幕上按住按钮 1 秒、同时按住左右 ⌘ 2 秒、倒计时结束（默认 90 秒，最长 10 分钟）、Deck 里点解锁、对助手说「解锁键盘」；合盖、睡眠、锁屏时自动解锁。
  Unlock by holding the on-screen button for 1 s, holding both ⌘ keys for 2 s, waiting for the timer (90 s by default, 10 min max), tapping Unlock in Deck, or telling the assistant; it also unlocks on lid close, sleep and screen lock.
- 锁屏面板跟 Deck 的皮肤走：新拟态或液态玻璃（macOS 26+），白天 / 夜里都有。
  The lock panel follows Deck's skin: neumorphic or Liquid Glass (macOS 26+), day and night.
- 设置 → 显示方式：自动 / 扩展屏 / 刘海，和菜单栏是同一个设置。皮肤选项「玻璃（原来的）」改名为「玻璃」。
  Settings → Display: Auto / External screen / Notch, the same setting as the menu bar. The skin option "Glass (original)" is now just "Glass".

#### 怎么实现 / How it works

- 拦按键的是 DeckHands 的清洁模式（`manager/hands/CleanMode.swift`，`--clean` 单独起进程），沿用 DeckHands 的辅助功能权限，不用另外授权。写法参考 MIT 开源的 [Lockpaw](https://github.com/sorkila/lockpaw) 和 [keylock](https://github.com/kfv/keylock)。更新后要重新跑一次 `manager/hands/build.sh`。
  Keys are blocked by DeckHands' clean mode (`manager/hands/CleanMode.swift`, a separate `--clean` process) using DeckHands' existing Accessibility permission. Approach based on the MIT-licensed Lockpaw and keylock. Re-run `manager/hands/build.sh` after updating.

#### 安全和稳定 / Safety and stability

- 绝不锁死：进程卡住 6 秒（心跳停了）或过了截止时间，服务端直接结束它；进程一退出系统就撤掉拦截。结束后恢复被误按的 Caps Lock。
  It can't get stuck: if the process hangs for 6 s (no heartbeat) or runs past its deadline, the server ends it, and the system drops the block as soon as the process exits. Caps Lock is restored afterwards.
- 接口只认本机加口令；只会给确认过的 `DeckHands --clean` 进程发信号，不会误杀别的程序。
  The endpoints accept local requests with the token only, and signals go only to a verified `DeckHands --clean` process.
- 修复：工具箱里的报错之前显示成灰字，现在是醒目的提醒色。 / Fix: toolbox errors were shown in grey; they now use the warning color.

### 更新 / Updating

拉代码后：重新跑一次 `manager/hands/build.sh`（键盘清洁要用），重启语音服务（`pkill -f service.server`，DeckVoice 会重新拉起），再退出 Deck、结束 8777 上的 `server.py`、重新打开 Deck。
After pulling: re-run `manager/hands/build.sh` (needed for keyboard cleaning), restart the voice service (`pkill -f service.server`; DeckVoice starts it again), then quit Deck, end `server.py` on port 8777 and reopen Deck.

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
