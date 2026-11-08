<div align="center">

<img src="docs/images/icon.png" width="128" alt="Deck 图标">

<h1>Deck</h1>

<p><b>免费开源的 MacBook 电脑管家</b><br>一块常亮的控制台 + 一个本地优先的语音助手。铺在外接屏、旧 iPad 上，或者从刘海里流下来。</p>

<p><b>中文</b> · <a href="README.en.md">English</a></p>

[![Release](https://img.shields.io/github/v/release/3520566741-YCLi/deck-for-mac?style=flat-square&label=%E7%89%88%E6%9C%AC&color=2f6fe0)](https://github.com/3520566741-YCLi/deck-for-mac/releases/latest)
[![License](https://img.shields.io/github/license/3520566741-YCLi/deck-for-mac?style=flat-square&label=%E8%AE%B8%E5%8F%AF)](LICENSE)
![Platform](https://img.shields.io/badge/macOS-14%2B%20%C2%B7%20Apple%20%E8%8A%AF%E7%89%87-111?style=flat-square&logo=apple)
![Local first](https://img.shields.io/badge/%E9%99%A4%E4%BA%86%E5%A4%A7%E6%A8%A1%E5%9E%8B-%E5%85%A8%E5%9C%A8%E6%9C%AC%E6%9C%BA-139a63?style=flat-square)
[![Setup](https://img.shields.io/badge/%E5%AE%89%E8%A3%85-%E4%BA%A4%E7%BB%99%E4%BD%A0%E7%9A%84%20AI-7259e0?style=flat-square)](AI_SETUP.md)
[![Stars](https://img.shields.io/github/stars/3520566741-YCLi/deck-for-mac?style=flat-square&label=Star&color=c27a0c)](https://github.com/3520566741-YCLi/deck-for-mac/stargazers)

<a href="#画廊"><img src="docs/images/banner.jpg" width="900" alt="Deck：控制台主页和刘海模式"></a>

<a href="https://github.com/3520566741-YCLi/deck-for-mac/releases/latest"><img src="https://img.shields.io/github/v/release/3520566741-YCLi/deck-for-mac?style=for-the-badge&label=%E4%B8%8B%E8%BD%BD%E6%9C%80%E6%96%B0%E7%89%88&logo=apple&logoColor=white&color=2f6fe0" alt="下载最新版" height="40"></a>
&nbsp;
<a href="CHANGELOG.md"><img src="https://img.shields.io/github/release-date/3520566741-YCLi/deck-for-mac?style=for-the-badge&label=%E6%9B%B4%E6%96%B0%E4%BA%8E&color=7259e0" alt="最近更新" height="40"></a>

<p><a href="#它能做什么">功能</a> · <a href="#三种放法">三种放法</a> · <a href="#画廊">画廊</a> · <a href="#下载和安装">下载和安装</a> · <a href="#最近更新">最近更新</a> · <a href="#常见问题">常见问题</a> · <a href="#参与贡献">参与贡献</a> · <a href="#-版本历史--releases">版本历史</a></p>

</div>

> [!NOTE]
> Deck 不是一键安装包。它由 Python 服务、网页、Swift 小应用和本地语音模型组成，**推荐把仓库交给你自己的 AI 编程助手，照着 [AI_SETUP.md](AI_SETUP.md) 装**，它会先问你要哪些功能。

## 它能做什么

| | 功能 | 一句话 |
|---|---|---|
| 🖥️ | **控制台** | 本地网页，铺在外接屏、旧 iPad 上，或从 MacBook 刘海弹出（⌥空格）。新拟态皮肤，白天 / 夜里自动切换，卡片可拖、可改大小。 |
| 📊 | **实时状态** | CPU、内存、GPU、磁盘、网速、最占资源的进程；Claude Code / Codex 还剩多少额度；DeepSeek 余额；GitHub 仓库。一张卡只回答一个问题。 |
| 💽 | **磁盘管家** | 点磁盘圆环：谁在变大、最近新增了什么、谁在狂写硬盘。 |
| 🎙️ | **语音助手** | 喊名字唤醒，本机识别、本机合成，只有「大脑」用云端大模型。声纹确认是你才听、才能打断。能读卡片和日报、看行情、管进程和应用、改 Deck 设置、联网搜、帮你点屏幕。 |
| 🗞️ | **资讯页** | 每天三期个人科技日报：前沿科技、开源热榜、AI 巨头动态、艺术 × 代码、自选股资讯（以长桥证券为例）。每天花费封顶 1 元。 |
| 🧹 | **工具箱 + 每日清理** | 卸载连残留、清缓存、找大文件和重复文件、应用更新。**只移到废纸篓，从不直接删除**，可一键放回。「小工具」页里有**键盘清洁**：锁住键盘（可选黑屏）方便擦，多种方式解锁，绝不锁死。 |

## 三种放法

同一个网页、同一套功能，放哪儿由你：**设置 → 显示方式**，或菜单栏 Deck 图标 → 显示方式，选 **自动 / 扩展屏 / 刘海**；⌥空格随时呼出。

| 外接屏 / 旧 iPad 铺满 | 从 MacBook 刘海弹出 |
|:---:|:---:|
| <img src="docs/images/home-day.png" alt="扩展屏铺满"> | <img src="docs/images/notch-l.png" alt="刘海模式"> |
| 插上副屏自动铺上去 | 碰一下刘海展开，移开自动收起 |

**刘海模式**有大、中、小三种尺寸，横排（左右翻页）和竖排（上下滑）两种排法，在刘海里也能直接和助手对话。

| 中号 | 小号 |
|:---:|:---:|
| <img src="docs/images/notch-m.png" alt="刘海模式中号"> | <img src="docs/images/notch-s.png" alt="刘海模式小号"> |

## 画廊

#### 夜里自动换深色
<img src="docs/images/home-night.png" width="860" alt="夜里的主页">

#### 刘海里翻页、滑动、对话
| 横排 · 左右翻页 | 竖排 · 上下滑动 |
|:---:|:---:|
| <img src="docs/images/notch-row.png" alt="横排"> | <img src="docs/images/notch-col.png" alt="竖排"> |

<img src="docs/images/notch-vesper.png" width="860" alt="在刘海里和助手对话">

#### 语音助手：喊名字就来
<img src="docs/images/assistant.png" width="860" alt="和助手对话">

#### 「读一下今天的日报」：助手用自己的声音播，逐句高亮
<img src="docs/images/vesper-brief.png" width="860" alt="助手播放日报">

#### 磁盘：谁在变大、最近新增、谁在写硬盘
<img src="docs/images/disk.png" width="860" alt="磁盘面板">

#### 工具箱和每日清理：只进废纸篓，一键放回
| 工具箱 | 每日清理 |
|:---:|:---:|
| <img src="docs/images/toolbox.png" alt="工具箱"> | <img src="docs/images/cleanup.png" alt="每日清理"> |

#### 小工具：擦键盘时把键盘锁住
| 键盘清洁 | 设置里切显示方式 |
|:---:|:---:|
| <img src="docs/images/toolbox-tools.png" alt="工具箱 · 小工具 · 键盘清洁"> | <img src="docs/images/settings-display.png" alt="设置 · 显示方式"> |
| 锁多久、黑屏、连触控板；按住屏幕按钮 1 秒、左右 ⌘ 同按 2 秒、倒计时、对助手说「解锁键盘」都能解开 | 自动 / 扩展屏 / 刘海，和菜单栏是同一个设置 |

#### 助手：名字自己起，声纹自己录
| 改名字 | 录声纹 |
|:---:|:---:|
| <img src="docs/images/settings-assistant.png" alt="设置 · 助手 · 名字"> | <img src="docs/images/settings-voiceprint.png" alt="设置 · 助手 · 声纹录入"> |
| 中英文名字都行，可填读法；先「体检」看好不好叫醒，改完唤醒当场换 | 一步步提示怎么说，录好一遍自动跳下一步；录完还能测唤醒率、会不会被别人冒充 |

#### 管家和 GitHub：哪个软件崩了、哪个仓库红了
| 管家 | GitHub |
|:---:|:---:|
| <img src="docs/images/butler.png" alt="管家页"> | <img src="docs/images/github.png" alt="GitHub 管家"> |

#### 资讯：每天三期个人科技日报
<img src="docs/images/news.png" width="860" alt="资讯页">

#### 设置：卡片想开就开，皮肤随时换
<img src="docs/images/settings.png" width="860" alt="设置">

> 截图里的应用名、仓库名和对话是示范内容，数字是真实运行时的数据。

## 下载和安装

**1. 下载**：打开 [最新版 Release](https://github.com/3520566741-YCLi/deck-for-mac/releases/latest)，在页面最下面的 **Assets** 里点 **Source code (zip)**，解压后把文件夹改名为 `deck-for-mac` 放到你的个人文件夹（`~/deck-for-mac`）。会用 git 的话更方便以后更新：

```bash
git clone https://github.com/3520566741-YCLi/deck-for-mac.git ~/deck-for-mac
```

> Deck 是 Python 服务 + 网页 + Swift 小应用 + 本地语音模型，每台 Mac 要装的东西不一样，所以没有 `.dmg` 安装包，下载的就是完整源码。

**2. 交给你的 AI 装**：打开你的 AI 编程助手（Claude Code、Codex、Cursor……），让它能在你的 Mac 上干活，把这句话发给它：

> 请读 ~/deck-for-mac/AI_SETUP.md（或 https://github.com/3520566741-YCLi/deck-for-mac 的 `AI_SETUP.md`），按里面的步骤在我的 Mac 上装好 Deck。先检查我的电脑、问我想要哪些功能，再一步一步来。

**3. 回答它的问题**（要哪些卡片、用哪个大模型、要不要语音、助手叫什么）。**API key 不要发给它**，它会给你一行命令，你自己在终端里把 key 存进钥匙串。

想自己装也可以，[AI_SETUP.md](AI_SETUP.md) 每一步都有命令。

### 更新到新版

点仓库右上角 **Watch → Custom → Releases**，出新版时 GitHub 会通知你。更新时对你的 AI 说：

> 按 ~/deck-for-mac/AI_SETUP.md 第 10 节，把 Deck 更新到最新版。

你的设置（`config.json`、`apps.json`）、聊天记录、记忆和声纹都不会被覆盖。

## 最近更新

**v0.2.0**（2026-11-08）

- 🎙️ **助手能读日报、能操作 Deck 的全部功能**：说「读一下今天的日报」就用它的声音念，逐句高亮；资讯各栏、自选股行情、进程、磁盘、GitHub、应用、工具箱、设置、显示方式、每日清理都能用嘴问、用嘴改。会改动东西的先确认，只进废纸篓。
- 👆 **每个按钮都有按压反馈和音效**：资讯页、管家页、对话页的按钮之前按了没声音，现在全都补上；「重编这一期」点了会转圈。
- ⌨️ **键盘清洁**：工具箱新增「小工具」页，锁住键盘方便擦，可选黑屏和连触控板一起锁，多种方式解锁，绝不锁死。
- 🪪 **助手名字和声纹自己设**：设置 →「助手」里给它起名字（唤醒当场换）、名字体检、引导式声纹录入、唤醒和声纹测试。
- 🖥️ **设置里切换显示方式**：自动 / 扩展屏 / 刘海，和菜单栏是同一个设置。

全部版本见下方 [版本历史](#-版本历史--releases)，详细改动见 [CHANGELOG.md](CHANGELOG.md)。

## 需要什么

- Apple 芯片的 Mac（M1 及以后）。语音要 macOS 14+；助手面板的系统液态玻璃要 macOS 26+（更早的系统自动换成网页面板）。
- 用助手要一个大模型 API key，默认 [DeepSeek](https://platform.deepseek.com/)（任何兼容 OpenAI、支持流式和工具调用的接口都行）。
- 语音要约 3 GB 磁盘放本地模型。
- 每个外部服务都列在 [docs/services.md](docs/services.md)，附 2026-11-07 的核查结果和核查方法。

## 原则

- **本地优先**：除了大模型，识别、合成、唤醒、声纹、数据采集都在你的 Mac 上。录音和声纹不上传。
- **只听 127.0.0.1**：所有服务只在本机；写操作要随机令牌 + 同源检查；挡 DNS 重绑定。
- **不花冤枉钱**：大模型只在你开口、点按钮、或你设好的时间调用。
- **改电脑先问你**：清理、结束进程、点按钮都先出确认卡。
- **只进废纸篓**：任何「删除」都只是移到废纸篓，可以放回。

## 路线图

- [x] 控制台：实时状态、AI 额度、DeepSeek 余额、GitHub、我的应用、工具箱、每日清理
- [x] 三种放法：扩展屏、刘海（大中小、横排竖排）、自动切换
- [x] 磁盘面板、资讯页、长桥自选股资讯
- [x] 语音助手：本机唤醒、声纹、识别、合成，可打断
- [x] DeckHands：用辅助功能 API 帮你点屏幕，风险操作先确认
- [x] 键盘清洁：锁键盘擦键盘，多重解锁
- [ ] 更多大模型预设（欢迎 PR）
- [ ] 英文界面（欢迎 PR）

## 常见问题

<details><summary><b>要花钱吗？</b></summary>

Deck 本身免费。只有助手的「大脑」调用云端大模型会花钱（默认 DeepSeek，用你自己的 key）；资讯页每天封顶 1 元，可调。不开助手和资讯页就完全不花钱。
</details>

<details><summary><b>我的数据会上传吗？</b></summary>

录音、声纹、文件内容、截图都不上传。发给大模型的只有你说的话、卡片摘要、记忆和新闻标题。详见 <a href="docs/services.md#隐私">docs/services.md</a>。
</details>

<details><summary><b>没有刘海 / 没有 iPad 能用吗？</b></summary>

能。没有刘海就用扩展屏模式铺在任何副屏上，或者直接在浏览器里打开 <code>http://127.0.0.1:8777</code>。
</details>

<details><summary><b>Intel Mac 能用吗？</b></summary>

目前只支持 Apple 芯片（M1 及以后）：原生壳按 arm64 编译，本地语音模型也要用 Apple GPU。
</details>

<details><summary><b>助手能改名字吗？</b></summary>

能。Deck 设置 →「助手」里直接改（英文或中文名字都行），改之前可以先「体检」一下好不好叫醒；改完在同一处重录声纹、做唤醒测试。见 <a href="AI_SETUP.md">AI_SETUP.md</a>。
</details>

<details><summary><b>股票只能用长桥吗？</b></summary>

代码以长桥证券为例（只要「自选」只读权限）。其他券商不一定提供面向个人的行情接口，接之前先按 <a href="docs/services.md">docs/services.md</a> 的三条核查。
</details>

<details><summary><b>会不会误删我的文件？</b></summary>

不会。所有「删除」都只是移到废纸篓，Deck 从不清空废纸篓，每日清理也能一键放回。
</details>

## 目录

```
manager/            管家服务（Python 标准库，:8777）+ 控制台网页
  monitor/          各卡片的数据采集
  assistant.py      助手：对话、工具、长期记忆
  newsroom/         资讯页（免费来源 → DeepSeek 一次编辑）
  voice/            语音：DeckVoice.app（Swift）+ 语音服务（Python, sherpa-onnx）
  hands/            DeckHands.app：辅助功能 API 点按
app/                Deck.app 原生壳（SwiftPM）：扩展屏铺满 / 刘海弹出
scripts/            Claude 状态栏桥接、界面自查脚本
docs/               服务清单、语音提速笔记、截图
```

## 参与贡献

欢迎 issue 和 PR！先看 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [行为准则](CODE_OF_CONDUCT.md)。安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。

## 文档

- [AI_SETUP.md](AI_SETUP.md)：给 AI 的安装和配置说明
- [docs/services.md](docs/services.md)：外部服务清单、核查方法、长桥证券案例
- [docs/voice-latency.md](docs/voice-latency.md)：语音助手怎么做到说完 2–3 秒出声
- [CHANGELOG.md](CHANGELOG.md)：更新记录

## 📦 版本历史 / Releases

| 版本 | 日期 | 这一版改了什么 | 下载 |
|---|---|---|---|
| **v0.2.0** `最新` | 2026-11-08 | 助手能读日报、能操作 Deck 全部功能；按钮反馈和音效；键盘清洁；助手名字和声纹自己设；设置里切换显示方式 · [详细](CHANGELOG.md#v020--2026-11-08) | [下载](https://github.com/3520566741-YCLi/deck-for-mac/releases/tag/v0.2.0) |
| v0.1.1 | 2026-11-08 | 修好叫不醒；说「查一下」会当场联网查；可选的语音看门狗 · [详细](CHANGELOG.md#v011--2026-11-08) | [下载](https://github.com/3520566741-YCLi/deck-for-mac/releases/tag/v0.1.1) |
| v0.1.0 | 2026-11-07 | 第一版公开发布：控制台、语音助手、资讯页、工具箱、刘海模式 · [详细](CHANGELOG.md#v010--2026-11-07) | [下载](https://github.com/3520566741-YCLi/deck-for-mac/releases/tag/v0.1.0) |

所有版本都一直保留在 [Releases](https://github.com/3520566741-YCLi/deck-for-mac/releases)，每个版本页的 **Assets** 里有当时的完整源码。

## 致谢

站在这些开源项目的肩膀上：[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)、[Qwen3-ASR / Qwen3-TTS](https://huggingface.co/Qwen)、[mlx-audio](https://github.com/Blaizzy/mlx-audio)、[3D-Speaker](https://github.com/modelscope/3D-Speaker)、[Silero VAD](https://github.com/snakers4/silero-vad)、[GridStack.js](https://github.com/gridstack/gridstack.js)、[React Bits](https://github.com/DavidHDev/react-bits)、[snd-lib](https://github.com/snd-lib/snd-lib)、[Kenney](https://kenney.nl/)、[Lockpaw](https://github.com/sorkila/lockpaw) 和 [keylock](https://github.com/kfv/keylock)（键盘清洁的写法参考）。完整清单见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 许可

[MIT](LICENSE) © Deck for Mac contributors。第三方组件各有许可，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

<div align="center"><sub>觉得有用就点个 ⭐，让更多人看到。</sub></div>
