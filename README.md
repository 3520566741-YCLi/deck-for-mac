<div align="center">

# Deck

**一块常亮的 Mac 控制台 + 一个本地优先的语音助手。把这个仓库丢给你的 AI，让它帮你在自己的 Mac 上搭出来。**

**An always-on Mac dashboard + a local-first voice assistant. Hand this repo to your AI and let it build one for your Mac.**

![macOS](https://img.shields.io/badge/macOS-Apple%20Silicon-black?logo=apple)
![Local first](https://img.shields.io/badge/local--first-only%20the%20LLM%20is%20cloud-2e8b57)
![AI setup](https://img.shields.io/badge/setup-by%20your%20AI-8a2be2)
![Status](https://img.shields.io/badge/status-early-orange)

</div>

---

## 这是什么 / What it is

Deck 是我给自己 Mac 做的小管家，现在把做法整理出来分享给朋友：

- 🖥️ **控制台**：一个本地网页，铺在扩展屏（比如旧 iPad）上，或者从刘海弹出。一张卡片只回答一个问题：Mac 现在忙不忙、Claude Code / Codex 还剩多少额度、DeepSeek 余额、GitHub 仓库、磁盘清理……
- 🎙️ **语音助手**：喊名字唤醒，本机识别、本机合成语音，只有「大脑」用云端大模型。能回答卡片上的数据、帮你开卡片、搜网页；改电脑的事一定先问你。
- 🧹 **工具箱**：卸载应用连残留、清缓存、找大文件，**只移到废纸篓，从不直接删除**。

Deck is the little butler I built for my own Mac, written up so friends can build theirs:

- 🖥️ **Dashboard**: a local web page shown on an extra screen (an old iPad works) or dropped down from the notch. Each card answers one question: is the Mac busy, how much Claude Code / Codex quota is left, DeepSeek balance, GitHub repos, disk cleanup…
- 🎙️ **Voice assistant**: wake it by name. Speech recognition and speech synthesis run on the Mac; only the "brain" is a cloud LLM. It reads card data, opens cards, searches the web, and always asks before changing anything on your Mac.
- 🧹 **Toolbox**: uninstall apps with their leftovers, clear caches, find large files. **Everything goes to the Trash, nothing is deleted outright.**

## 怎么用 / How to use it

这个仓库**不是**一个装完就能用的安装包，而是一份写给 AI 看的「施工说明」。

1. 打开你的 AI 编程助手（Claude Code、Codex、Cursor……），让它在你的 Mac 上工作。
2. 把这句话发给它：

   > 请读 https://github.com/3520566741-YCLi/deck-for-mac 里的 `AI_SETUP.zh-CN.md`，按里面的步骤在我的 Mac 上搭一个 Deck。先问我想要哪些功能、用哪家语音和大模型，再动手。

3. 回答它的问题（要哪些卡片、用哪家大模型、要不要语音、助手叫什么名字），剩下的交给它。

This repo is **not** a ready-made installer. It is a build guide written for AI coding assistants. Tell yours: *"Read `AI_SETUP.md` in https://github.com/3520566741-YCLi/deck-for-mac and build me a Deck. Ask me what I want first."* Then answer its questions.

## 里面有什么 / What's inside

| 文件 / File | 内容 / What |
|---|---|
| [AI_SETUP.zh-CN.md](AI_SETUP.zh-CN.md) · [AI_SETUP.md](AI_SETUP.md) | 给 AI 的完整施工说明：先问什么、架构、每张卡的数据从哪来、语音用哪些开源组件、安全规矩、验收清单 / The full build guide for your AI |
| [docs/voice-latency.zh-CN.md](docs/voice-latency.zh-CN.md) · [docs/voice-latency.md](docs/voice-latency.md) | 语音助手提速：从说完到出声 2–3 秒是怎么做到的 / How the voice loop gets to 2–3 s |

更多素材（参考代码、界面）会慢慢补上。More reference code and UI will be added over time.

## 原则 / Principles

- **本地优先 / Local first**：除了大模型，识别、合成、唤醒、数据采集都在你自己的 Mac 上。Everything except the LLM runs on your Mac.
- **只听 127.0.0.1 / Localhost only**：所有服务只在本机，不开公网端口。No service is exposed to the network.
- **不花冤枉钱 / No idle spending**：大模型只在你开口或点按钮时调用，从不 24 小时跑。The LLM is only called when you talk or click.
- **改电脑先问你 / Ask before acting**：清理、结束进程、点按钮这类操作都先出确认。Anything that changes your Mac asks first.
- **只进废纸篓 / Trash, never delete**：任何「删除」都只是移到废纸篓，可以放回。Every removal can be undone.

## 许可 / License

[MIT](LICENSE)。引用到的第三方开源项目各有各的许可，`AI_SETUP` 里都标了。Third-party projects keep their own licenses, listed in `AI_SETUP`.
