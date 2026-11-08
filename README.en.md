<div align="center">

<img src="docs/images/icon.png" width="128" alt="Deck icon">

<h1>Deck</h1>

<p><b>A free, open-source butler for your MacBook</b><br>An always-on dashboard plus a local-first voice assistant. Put it on an external screen or an old iPad, or let it drop down from the notch.</p>

<p><a href="README.md">中文</a> · <b>English</b></p>

[![Release](https://img.shields.io/github/v/release/3520566741-YCLi/deck-for-mac?style=flat-square&color=2f6fe0)](https://github.com/3520566741-YCLi/deck-for-mac/releases/latest)
[![License](https://img.shields.io/github/license/3520566741-YCLi/deck-for-mac?style=flat-square)](LICENSE)
![Platform](https://img.shields.io/badge/macOS-14%2B%20%C2%B7%20Apple%20silicon-111?style=flat-square&logo=apple)
![Local first](https://img.shields.io/badge/local--first-only%20the%20LLM%20is%20cloud-139a63?style=flat-square)
[![Setup](https://img.shields.io/badge/setup-by%20your%20AI-7259e0?style=flat-square)](AI_SETUP.en.md)
[![Stars](https://img.shields.io/github/stars/3520566741-YCLi/deck-for-mac?style=flat-square&color=c27a0c)](https://github.com/3520566741-YCLi/deck-for-mac/stargazers)

<a href="#gallery"><img src="docs/images/banner.jpg" width="900" alt="Deck: dashboard and notch mode"></a>

<a href="https://github.com/3520566741-YCLi/deck-for-mac/releases/latest"><img src="https://img.shields.io/github/v/release/3520566741-YCLi/deck-for-mac?style=for-the-badge&label=Download&logo=apple&logoColor=white&color=2f6fe0" alt="Download the latest release" height="40"></a>
&nbsp;
<a href="CHANGELOG.md"><img src="https://img.shields.io/github/release-date/3520566741-YCLi/deck-for-mac?style=for-the-badge&label=Updated&color=7259e0" alt="Last updated" height="40"></a>

<p><a href="#what-it-does">Features</a> · <a href="#three-ways-to-show-it">Display modes</a> · <a href="#gallery">Gallery</a> · <a href="#download-and-install">Download &amp; install</a> · <a href="#whats-new">What's new</a> · <a href="#faq">FAQ</a> · <a href="#contributing">Contributing</a></p>

</div>

> [!NOTE]
> Deck is not a one-click installer. It is a Python service, a web page, small Swift apps and local voice models, so **the recommended way is to hand this repo to your own AI coding assistant and have it follow [AI_SETUP.en.md](AI_SETUP.en.md)**. It will ask what you want first.

## What it does

> The interface is in Chinese for now. Your AI can translate the UI strings while it sets Deck up for you.

| | Feature | In one line |
|---|---|---|
| 🖥️ | **Dashboard** | A local web page shown on an external screen or an old iPad, or dropped down from the MacBook notch (⌥Space). Neumorphic skin, automatic day / night, cards you can drag and resize. |
| 📊 | **Live status** | CPU, memory, GPU, disk, network, top processes; Claude Code / Codex quota left; DeepSeek balance; GitHub repos. Each card answers one question. |
| 💽 | **Disk watch** | Tap the disk ring: what is growing, what was added recently, who is writing to the disk. |
| 🎙️ | **Voice assistant** | Wake it by name. Speech recognition and synthesis run on your Mac; only the "brain" is a cloud LLM. A voiceprint makes sure only you can wake or interrupt it. It reads card data, opens cards, searches the web and can click things for you. |
| 🗞️ | **News page** | A personal tech paper three times a day: frontier tech, trending open source, AI labs, art × code, and news on your watchlist (Longbridge Securities as the example broker). Capped at about ¥1 a day. |
| 🧹 | **Toolbox + daily cleanup** | Uninstall apps with leftovers, clear caches, find large and duplicate files, update apps. **Everything goes to the Trash, nothing is deleted outright**, and one tap puts it back. |

## Three ways to show it

One web page, one set of features, wherever you like: menu bar Deck icon → **Display: Auto / External screen / Notch**, or ⌥Space at any time.

| External screen / old iPad | Dropped down from the notch |
|:---:|:---:|
| <img src="docs/images/home-day.png" alt="External screen"> | <img src="docs/images/notch-l.png" alt="Notch mode"> |
| Fills a second screen when one is plugged in | Hover the notch to open, move away to close |

**Notch mode** comes in large, medium and small, laid out in a row (swipe left / right) or a column (scroll), and you can talk to the assistant right inside it.

| Medium | Small |
|:---:|:---:|
| <img src="docs/images/notch-m.png" alt="Notch mode, medium"> | <img src="docs/images/notch-s.png" alt="Notch mode, small"> |

## Gallery

#### Dark at night, automatically
<img src="docs/images/home-night.png" width="860" alt="Dashboard at night">

#### Swipe, scroll and chat in the notch
| Row · swipe | Column · scroll |
|:---:|:---:|
| <img src="docs/images/notch-row.png" alt="Row layout"> | <img src="docs/images/notch-col.png" alt="Column layout"> |

<img src="docs/images/notch-vesper.png" width="860" alt="Chatting with the assistant in the notch">

#### Voice assistant: call its name
<img src="docs/images/assistant.png" width="860" alt="Talking to the assistant">

#### Disk: what grows, what's new, who writes
<img src="docs/images/disk.png" width="860" alt="Disk panel">

#### Toolbox and daily cleanup: Trash only, one tap to undo
| Toolbox | Daily cleanup |
|:---:|:---:|
| <img src="docs/images/toolbox.png" alt="Toolbox"> | <img src="docs/images/cleanup.png" alt="Daily cleanup"> |

#### Butler and GitHub: which app crashed, which repo went red
| Butler | GitHub |
|:---:|:---:|
| <img src="docs/images/butler.png" alt="Butler page"> | <img src="docs/images/github.png" alt="GitHub manager"> |

#### News: a personal tech paper three times a day
<img src="docs/images/news.png" width="860" alt="News page">

#### Settings: turn cards on and off, switch skins
<img src="docs/images/settings.png" width="860" alt="Settings">

> App names, repo names and conversations in the screenshots are demo content; the numbers come from a real running Mac.

## Download and install

**1. Download:** open the [latest release](https://github.com/3520566741-YCLi/deck-for-mac/releases/latest), click **Source code (zip)** under **Assets** at the bottom, unzip it, rename the folder to `deck-for-mac` and put it in your home folder (`~/deck-for-mac`). With git, updating later is easier:

```bash
git clone https://github.com/3520566741-YCLi/deck-for-mac.git ~/deck-for-mac
```

> Deck is a Python service, a web page, small Swift apps and local voice models, and every Mac needs a different subset, so there is no `.dmg`: the download is the full source.

**2. Hand it to your AI:** open your AI coding assistant (Claude Code, Codex, Cursor…), let it work on your Mac, and send it:

> Read ~/deck-for-mac/AI_SETUP.en.md (or `AI_SETUP.en.md` in https://github.com/3520566741-YCLi/deck-for-mac) and install Deck on my Mac step by step. Check my machine and ask me what I want first.

**3. Answer its questions** (which cards, which LLM, voice or not, the assistant's name). **Never paste an API key into the chat**; it will give you one command to store the key in the macOS Keychain yourself.

You can also follow [AI_SETUP.en.md](AI_SETUP.en.md) by hand; every step has its commands.

### Updating

Click **Watch → Custom → Releases** at the top right of the repo to get notified of new versions. To update, tell your AI:

> Update Deck to the latest version following section 10 of ~/deck-for-mac/AI_SETUP.en.md.

Your settings (`config.json`, `apps.json`), chat history, memories and voiceprint are never overwritten.

## What's new

**v0.1.1** (2026-11-08): the voice assistant wakes up more reliably (a second, name-based wake path and a self-test every 10 minutes); "let me check" now means it searches the web right away; optional DeckVoice watchdog. Every version is in [CHANGELOG.md](CHANGELOG.md) and on [Releases](https://github.com/3520566741-YCLi/deck-for-mac/releases); old versions are kept.

## Requirements

- A Mac with Apple silicon (M1 or later). Voice needs macOS 14+; the system Liquid Glass assistant panel needs macOS 26+ (older systems get a web-drawn panel).
- An LLM API key for the assistant. The default is [DeepSeek](https://platform.deepseek.com/); any OpenAI-compatible API with streaming and tool calls works.
- About 3 GB of disk for local voice models.
- Every outside service is listed in [docs/services.en.md](docs/services.en.md), with what we found on 2026-11-07 and how to re-check it.

## Principles

- **Local first**: wake word, voiceprint, recognition, synthesis and data collection all run on your Mac. Recordings and voiceprints never leave it.
- **Localhost only**: every service listens on 127.0.0.1; writes need a random token and a same-origin request; DNS rebinding is blocked.
- **No idle spending**: the LLM is called only when you talk, click, or at times you set.
- **Ask before acting**: cleanup, killing processes and clicking all show a confirmation first.
- **Trash, never delete**: every removal is a move to the Trash and can be undone.

## Roadmap

- [x] Dashboard: live status, AI quota, DeepSeek balance, GitHub, my apps, toolbox, daily cleanup
- [x] Three display modes: external screen, notch (large / medium / small, row / column), auto
- [x] Disk panel, news page, Longbridge watchlist news
- [x] Voice assistant: local wake word, voiceprint, recognition and synthesis, barge-in
- [x] DeckHands: clicks via the Accessibility API, risky steps confirmed first
- [ ] More LLM presets (PRs welcome)
- [ ] English UI (PRs welcome)

## FAQ

<details><summary><b>Does it cost anything?</b></summary>

Deck is free. Only the assistant's "brain" calls a cloud LLM (DeepSeek by default, with your own key); the news page is capped at about ¥1 a day and adjustable. Without the assistant and news page it costs nothing.
</details>

<details><summary><b>Is my data uploaded?</b></summary>

Recordings, voiceprints, file contents and screenshots never leave your Mac. Only what you say, card summaries, memories and news headlines go to the LLM. See <a href="docs/services.en.md#privacy">docs/services.en.md</a>.
</details>

<details><summary><b>No notch, no iPad?</b></summary>

Use external-screen mode on any second screen, or just open <code>http://127.0.0.1:8777</code> in a browser.
</details>

<details><summary><b>Intel Macs?</b></summary>

Apple silicon only for now (M1 and later): the native shell is built for arm64 and the local voice models use the Apple GPU.
</details>

<details><summary><b>Can I rename the assistant?</b></summary>

Yes: edit <code>wake_name.txt</code> and record your voiceprint again. See <a href="AI_SETUP.en.md">AI_SETUP.en.md</a>.
</details>

<details><summary><b>Only Longbridge for stocks?</b></summary>

The code uses Longbridge Securities as the example (read-only watchlist scope). Other brokers may not offer a market-data API for individuals; check the three points in <a href="docs/services.en.md">docs/services.en.md</a> first.
</details>

<details><summary><b>Could it delete my files?</b></summary>

No. Every removal is a move to the Trash, Deck never empties the Trash, and daily cleanup has a one-tap undo.
</details>

## Layout

```
manager/            Butler service (Python stdlib, :8777) + dashboard page
  monitor/          Data collectors for each card
  assistant.py      Assistant: chat, tools, long-term memory
  newsroom/         News page (free sources → one DeepSeek editing pass)
  voice/            Voice: DeckVoice.app (Swift) + voice service (Python, sherpa-onnx)
  hands/            DeckHands.app: clicks through the Accessibility API
app/                Deck.app native shell (SwiftPM): fills an external screen / drops from the notch
scripts/            Claude status line bridge, UI self-check script
docs/               Service list, voice latency notes, screenshots
```

## Contributing

Issues and PRs are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md). Report security issues privately as described in [SECURITY.md](SECURITY.md).

## Docs

- [AI_SETUP.en.md](AI_SETUP.en.md): install and configuration guide for AI assistants
- [docs/services.en.md](docs/services.en.md): outside services, how to verify them, the Longbridge example
- [docs/voice-latency.en.md](docs/voice-latency.en.md): how the assistant starts speaking 2–3 s after you stop
- [CHANGELOG.md](CHANGELOG.md): release notes

## Acknowledgements

Built on [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), [Qwen3-ASR / Qwen3-TTS](https://huggingface.co/Qwen), [mlx-audio](https://github.com/Blaizzy/mlx-audio), [3D-Speaker](https://github.com/modelscope/3D-Speaker), [Silero VAD](https://github.com/snakers4/silero-vad), [GridStack.js](https://github.com/gridstack/gridstack.js), [React Bits](https://github.com/DavidHDev/react-bits), [snd-lib](https://github.com/snd-lib/snd-lib) and [Kenney](https://kenney.nl/). Full list in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## License

[MIT](LICENSE) © Deck for Mac contributors. Third-party components keep their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

<div align="center"><sub>If Deck helps you, a ⭐ helps others find it.</sub></div>
