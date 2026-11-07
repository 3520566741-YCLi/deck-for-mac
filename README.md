<div align="center">

<img src="docs/images/icon.png" width="96" alt="Deck 图标">

# Deck

**免费开源的 MacBook 电脑管家：一块常亮的控制台 + 一个本地优先的语音助手。**

**中文** · [English](README.en.md)

![macOS](https://img.shields.io/badge/macOS-Apple%20芯片-black?logo=apple)
![License](https://img.shields.io/badge/license-MIT-blue)
![Local first](https://img.shields.io/badge/除了大模型-全在本机-2e8b57)
![Setup](https://img.shields.io/badge/安装-交给你的%20AI-8a2be2)
![Version](https://img.shields.io/badge/version-0.1.0-orange)

<img src="docs/images/home-day.png" width="860" alt="Deck 控制台主页（白天）">

</div>

## 它能做什么

| | 功能 | 一句话 |
|---|---|---|
| 🖥️ | **控制台** | 本地网页，铺在外接屏、旧 iPad 上，或从 MacBook 刘海弹出（⌥空格）。新拟态皮肤，白天 / 夜里自动切换，卡片可拖、可改大小。 |
| 📊 | **实时状态** | CPU、内存、GPU、磁盘、网速、最占资源的进程；Claude Code / Codex 还剩多少额度；DeepSeek 余额；GitHub 仓库。一张卡只回答一个问题。 |
| 💽 | **磁盘管家** | 点磁盘圆环：谁在变大、最近新增了什么、谁在狂写硬盘。 |
| 🎙️ | **语音助手** | 喊名字唤醒，本机识别、本机合成，只有「大脑」用云端大模型。声纹确认是你才听、才能打断。能读卡片数据、打开卡片、联网搜、帮你点屏幕。 |
| 🗞️ | **资讯页** | 每天三期个人科技日报：前沿科技、开源热榜、AI 巨头动态、艺术 × 代码、自选股资讯（以长桥证券为例）。每天花费封顶 1 元。 |
| 🧹 | **工具箱 + 每日清理** | 卸载连残留、清缓存、找大文件和重复文件、应用更新。**只移到废纸篓，从不直接删除**，可一键放回。 |

<table>
<tr>
<td><img src="docs/images/home-night.png" alt="夜里的主页"></td>
<td><img src="docs/images/notch.png" alt="从刘海弹出"></td>
</tr>
<tr>
<td><img src="docs/images/disk.png" alt="磁盘面板"></td>
<td><img src="docs/images/news.png" alt="资讯页"></td>
</tr>
<tr>
<td><img src="docs/images/assistant.png" alt="和 Vesper 对话"></td>
<td><img src="docs/images/butler.png" alt="管家页"></td>
</tr>
</table>

## 怎么装：交给你的 AI

Deck 由好几块组成（Python 服务、网页、Swift 小应用、本地语音模型），每台 Mac 情况不一样，所以我们不做一键安装包，而是写了一份**给 AI 看的手把手说明**，让你自己的 AI 编程助手照着装。

1. 打开你的 AI 编程助手（Claude Code、Codex、Cursor……），让它能在你的 Mac 上干活。
2. 把这句话发给它：

   > 请读 https://github.com/3520566741-YCLi/deck-for-mac 的 `AI_SETUP.md`，按里面的步骤在我的 Mac 上装好 Deck。先检查我的电脑、问我想要哪些功能，再一步一步来。

3. 回答它的问题（要哪些卡片、用哪个大模型、要不要语音、助手叫什么）。**API key 不要发给它**，它会给你一行命令，你自己在终端里把 key 存进钥匙串。

想自己装也可以，[AI_SETUP.md](AI_SETUP.md) 每一步都有命令。

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

## 文档

- [AI_SETUP.md](AI_SETUP.md)：给 AI 的安装和配置说明
- [docs/services.md](docs/services.md)：外部服务清单、核查方法、长桥证券案例
- [docs/voice-latency.md](docs/voice-latency.md)：语音助手怎么做到说完 2–3 秒出声
- [SECURITY.md](SECURITY.md)：安全设计和怎么报告漏洞
- [CHANGELOG.md](CHANGELOG.md)：更新记录

## 许可

[MIT](LICENSE)。第三方组件（GridStack、React Bits、按键音效、语音模型）各有许可，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
