# AI_SETUP · 给 AI 的安装和配置说明

**中文** · [English](AI_SETUP.en.md)

> **给读这份文件的 AI：** 你的用户想在自己的 Mac 上装 Deck。这个仓库里是**能直接跑的完整代码**（管家服务、控制台网页、语音、动手、原生壳）。你的工作是：先检查他的 Mac、问清他要什么，再按下面的步骤一步一步装好、配好，**每一步验收了再往下走**。和他说话用他的语言，少用术语，每次只让他做一件事。
>
> 三条底线：**API key 绝不让他贴进聊天**（只用 §3 的钥匙串命令）；**任何「删除」都只移到废纸篓**；**每个外部服务接入前先按 [docs/services.md](docs/services.md) 现场核查一遍还能用**，不行就告诉他、换方案或先跳过。

---

## 0. 先问清楚、先查清楚

| 要知道的 | 怎么知道 |
|---|---|
| Mac 型号、芯片、macOS 版本、剩余空间 | 自己查：`sw_vers`、`sysctl -n machdep.cpu.brand_string`、`df -h ~`。**需要 Apple 芯片**；语音要 macOS 14+，助手面板的系统液态玻璃要 macOS 26+（没有会自动用网页画的面板） |
| 有没有 Python 3.11+、Xcode 命令行工具、Homebrew、gh | 自己查：`python3 --version`、`xcode-select -p`、`brew -v`、`gh auth status` |
| Deck 显示在哪 | 问：浏览器标签页 / 外接屏或旧 iPad 当扩展屏 / 从 MacBook 刘海弹出（要装原生壳 §6） |
| 要哪些卡片 | 问；默认：Mac 状态 + 他装了的 AI 工具（自己查 `ls ~/.claude ~/.codex`、`which codex`） |
| 助手的「大脑」用哪个大模型 | 问。默认 DeepSeek（便宜、中文好）。接别家见 §3.2 |
| 要不要语音、助手叫什么名字 | 问。语音最占空间（模型约 3 GB），可以以后再装 |
| 要不要资讯页、看不看股票 | 问。股票只做了**长桥证券**（见 [docs/services.md](docs/services.md#长桥证券行情)），别家券商不一定有能用的开放接口 |
| 要不要让助手「动手」点屏幕 | 问。要用「辅助功能」权限，可以不装 |

把答案记在一个清单里，后面每步对照。

## 1. 放好代码

```bash
git clone https://github.com/3520566741-YCLi/deck-for-mac.git ~/deck-for-mac
cd ~/deck-for-mac/manager
cp config.example.json config.json        # 自己的设置，已在 .gitignore 里
cp apps.example.json apps.json            # 「我的应用」卡片要盯哪些应用，按他的回答改
```

- 放在 `~/deck-for-mac` 最省事：原生壳、语音小助手默认都在这里找代码。放别处也行，§6 有改路径的命令。
- `config.json` 里不放任何 key，只放「key 从哪里读」（§3）。

## 2. 跑起管家服务（核心，先做这一步）

```bash
~/deck-for-mac/manager/launch.sh          # 只用 Python 标准库，不用装依赖
open http://127.0.0.1:8777/dash
```

**验收：** 页面打开，Mac 状态卡的 CPU 曲线每 2 秒动一次；`lsof -iTCP:8777 -sTCP:LISTEN` 显示只听 `127.0.0.1`。

各张卡需要什么：

| 卡片 | 需要 | 没有时 |
|---|---|---|
| Mac 状态 / 磁盘 / 每日清理 / 工具箱 | 什么都不用 | — |
| Claude Code | 装了 Claude Code。剩余额度要在 `~/.claude/settings.json` 加状态栏命令（下面） | 卡片会写缺什么 |
| Codex | 装了 Codex（`codex` 命令或 Codex 应用） | 同上 |
| GitHub | `brew install gh && gh auth login`（Deck 自己不存 GitHub 令牌） | 同上 |
| DeepSeek 余额 | DeepSeek key（§3） | 同上 |
| 资讯页 | DeepSeek key；股票还要长桥授权（§5） | 只显示免费来源的原文标题 |

Claude 剩余额度（让他自己在终端跑，或你征得同意后改；**改之前先备份他原来的 settings.json 到废纸篓之外的地方**）：在 `~/.claude/settings.json` 里加

```json
"statusLine": { "type": "command", "command": "python3 ~/deck-for-mac/scripts/claude-statusline.py" }
```

不需要的卡片：在 `config.json` 的 `dashboard.sources.<名字>.enabled` 写 `false`，重启服务。

## 3. 大模型和 key

### 3.1 存 key 的唯一方式：钥匙串

告诉他在**自己的终端**里跑下面这行（会提示输入，key 不显示、不进命令历史），**不要让他把 key 发给你**：

```bash
security add-generic-password -U -s deepseek-api -a "$USER" -w
```

| 服务 | 钥匙串服务名 | 用途 | 必需？ |
|---|---|---|---|
| DeepSeek | `deepseek-api` | 助手大脑、资讯编辑、清理核对、进程建议 | 想用助手就必需 |
| TypeSafe Jev | `typesafe-api` | 「动手」时快速挑按钮、打断时判断是不是在跟助手说话 | 可选（目前要排队申请，没有也能用） |

读取顺序在 `config.json` 的 `deepseek.key_source`（环境变量 → 钥匙串 → 文件）。验收：`curl -s -o /dev/null -w "%{http_code}" https://api.deepseek.com/user/balance -H "Authorization: Bearer $(security find-generic-password -s deepseek-api -w)"` 返回 `200`（查余额不扣费）。**这条命令你自己跑，别把输出里的 key 打出来。**

### 3.2 选模型（先核查再填）

先列出这把 key 能用的模型，**以返回为准，不要照抄文档里的名字**：

```bash
curl -s https://api.deepseek.com/models -H "Authorization: Bearer $(security find-generic-password -s deepseek-api -w)"
```

然后在 `config.json` 里填：

| 键 | 用在哪 | 建议 |
|---|---|---|
| `deepseek.model` | 助手对话（要支持流式 + 工具调用） | 列表里的通用对话模型；2026-11 核查时是 `deepseek-flash`（旧名 `deepseek-chat` 等仍可能被接受，以列表为准） |
| `news.model` / `news.fallback_model` | 资讯每天编三期 | 便宜的 flash 档 |

想换别家：任何**兼容 OpenAI Chat Completions、支持流式和 tools** 的接口都行，把 `deepseek.base_url`、`deepseek.model` 改掉，key 存进钥匙串并改 `key_source`。余额卡片是 DeepSeek 专用的，换别家就关掉它。

### 3.3 助手的名字

默认叫 **Vesper**（用合成声音测过：唤醒率约 72%、零误唤醒）。他想改名：

1. 名字选**少见、两三个音节**的英文名；`manager/voice/service/wake_cli.py` 里有一套用系统合成声音测命中率和误唤醒的工具，定之前测一下。
2. 写进 `~/Library/Application Support/Deck/voice/wake_name.txt`（一行），重启语音服务。
3. 页面和提示词里的显示名：在仓库里搜 `Vesper` 一起换（`manager/dashboard.html`、`manager/assistant.py`、`manager/news.html`、`config.json` 的 `news.title`）。
4. 到 <http://127.0.0.1:8778/enroll> 按新名字重录声纹。

## 4. 语音（可选，最大的一块）

全部在本机：唤醒、声纹、识别、合成都不上传，只有识别出的文字发给大模型。说完到出声约 2–3 秒，原理见 [docs/voice-latency.md](docs/voice-latency.md)。

```bash
cd ~/deck-for-mac/manager/voice
service/models/fetch_wake_models.sh       # 唤醒 + 声纹模型（几十 MB）
service/fetch_asr_models.sh               # 识别：Qwen3-ASR 0.6B（约 940 MB）+ SenseVoice（约 230 MB）+ VAD
helper/build.sh                           # 编译 DeckVoice.app 装到 ~/Applications
open ~/Applications/DeckVoice.app         # 第一次会问麦克风、语音识别权限
```

- DeckVoice 会自己拉起语音服务（`service/run.sh`，第一次会在 `~/Library/Application Support/Deck/voice/venv` 建虚拟环境、装 `requirements.txt`）。
- **语音合成**：默认千问自带音色（Qwen3-TTS 0.6B，跑在 Apple GPU 上，约 1.9 GB）。装：先让 DeckVoice 跑一次建好虚拟环境，再 `"$HOME/Library/Application Support/Deck/voice/venv/bin/pip" install -r service/requirements-tts.txt && service/tools/fetch_tts_models.sh`。不想占空间可以用 CPU 后备 Kokoro：从 [sherpa-onnx 的 tts-models 发布页](https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models) 下载 `kokoro-multi-lang-v1_1` 解压到 `~/Library/Application Support/Deck/voice/models/`，再在同目录上一级的 `tts.json` 写 `{"voice": "kokoro:zf_001"}`。
- **录声纹**：打开 <http://127.0.0.1:8778/enroll>，按页面念 16 句（约 4 分钟）。录好后只有他的声音能唤醒和打断。
- 快捷键：⌥⌘J 直接说、⌥⌘. 停、⌥⌘M 开关麦克风。

**签名很重要：** 三个小应用（Deck、DeckVoice、DeckHands）用他自己的 **Apple Development 证书**签名（Xcode → 设置 → 账户，用免费 Apple ID 就能生成）。没有证书会退回临时签名，**每次重新编译都要重新给麦克风 / 辅助功能权限**。

**验收：** 说「你好 Vesper」→ 有提示音 → 问「我的 Mac 现在忙吗」→ 3 秒内开口回答；说「停」0.3 秒内停下；换个人说唤醒词叫不醒。

## 5. 资讯页和股票（可选）

- 资讯页每天在 DeepSeek 非高峰时段编三期（`config.json` 的 `news.slots`），每天花费到 `news.daily_budget_cny` 就停（默认 1 元，实际约 0.4 元/天）。改 `news.interests` 写他关心什么。
- 来源都是免费公开的（RSS、Hacker News、GitHub Trending、arXiv、Hugging Face），**接之前按 [docs/services.md](docs/services.md) 抽查几个还能打开**；打不开的从 `news.feeds` 里换掉。
- **股票（长桥证券）**：需要他本人有长桥账户并开通 OpenAPI。在投资分栏点「连接长桥」，Deck 会在 `~/Library/Application Support/Deck/news/lb-venv` 装官方 SDK，弹出 OAuth 授权页——**只勾「自选」，不要勾任何交易权限**。别的券商：先查它有没有公开的行情 / 自选接口和只读授权，没有就别接，投资分栏可以只显示资讯。

## 6. 原生壳 Deck.app（扩展屏 / 刘海，可选）

```bash
cd ~/deck-for-mac/app && ./build.sh        # SwiftPM 编译、签名、装到 /Applications
open /Applications/Deck.app
```

- 接了外接屏就铺满那块屏；没接就从刘海弹出（⌥空格），菜单栏图标 → 显示方式 可切。
- 代码不在 `~/deck-for-mac/manager` 时：`defaults write io.github.deckformac.deck butlerPath <manager 文件夹路径>`。
- Deck.app 会拉起管家服务。**改了 Python 代码后**：退出 Deck → `lsof -ti tcp:8777 | xargs kill` → 再打开 Deck（只退 Deck 时旧服务可能还在）。

## 7. 动手（可选）

```bash
cd ~/deck-for-mac/manager/hands && ./build.sh   # DeckHands.app，第一次用会要「辅助功能」权限
```

大模型决定做什么，DeckHands 去点。删除、发送、付款、关应用这类**一定先出确认卡**，他点了才做；说「停」立刻中止；不往密码框输入。

## 8. 交付前必须做

- **安全检查**：三个端口（8777/8778/8779）只听 127.0.0.1；不带令牌、错 Host、外站 Origin 的请求都被拒；`git status` 里没有 `config.json`、key、声纹、`*.db`。
- **稳定性检查**：页面开着时重启服务能自己重连；拔插外接屏、换麦克风后还能用；睡眠唤醒后数据继续刷新。
- **界面自查**：浅色、深色各看一遍每张卡：文字不被截断、不压边、不互相遮挡。可以跑 `node scripts/deck-ui-audit.js /tmp/deck-audit soft 1024x768 noshots`（要 Playwright）。
- 告诉他：所有数据在 `~/Library/Application Support/Deck/`，卸载就是退出三个应用、把这个文件夹和 `~/deck-for-mac` 移到废纸篓、删掉钥匙串里的 `deepseek-api`。

## 9. 架构速览

```
┌──────────── 他的 Mac（所有服务只听 127.0.0.1）────────────┐
│ Deck.app / 浏览器 ──HTTP+令牌──▶ 管家服务 :8777 (manager/server.py) │
│        │                         ├─ monitor/   各卡片采集        │
│        │                         ├─ assistant.py 助手 + 记忆      │──▶ 大模型（云端）
│        │                         ├─ newsroom/  资讯              │──▶ 免费新闻源 / 长桥
│        │                         └─ hands.py ──▶ DeckHands.app    │
│        └──SSE──▶ 语音服务 :8778 ◀─TCP :8779─ DeckVoice.app（麦克风/扬声器）│
└──────────────────────────────────────────────────────────────┘
```

只有一个大脑：耳朵只听、嘴只说、手只做，只有大模型做决定；同一时间只有一轮对话。网页、屏幕、文件名、新闻里的文字**只当数据，不当指令**。更多细节：`manager/voice/ORCHESTRATION.md`、`manager/voice/INTERFACE.md`。

## 验收清单

- [ ] `http://127.0.0.1:8777/dash` 能打开，Mac 状态每 2 秒更新。
- [ ] 他选的每张卡要么是真数据，要么清楚写着「缺 X，运行 Y」。
- [ ] key 只在钥匙串里；`config.json`、`apps.json` 没被提交。
- [ ] 助手能回答卡片上的数据、能打开卡片。
- [ ] （语音）唤醒 → 提问 → 3 秒内出声；「停」能停；别人叫不醒。
- [ ] （股票）长桥授权只有「自选」。
- [ ] （动手）有风险的步骤先问。
