# 第三方组件 / Third-party notices

Deck 自己的代码是 [MIT](LICENSE)。下面这些第三方组件各有许可。
Deck's own code is [MIT](LICENSE). The third-party parts below keep their own licenses.

## 仓库里自带的 / Bundled in this repository

| 组件 / Component | 版本 / Version | 许可 / License | 在哪 / Where |
|---|---|---|---|
| [GridStack.js](https://github.com/gridstack/gridstack.js) | 14.0.0 | MIT | `manager/static/gridstack-all.js`, `gridstack.min.css`, `gridstack.LICENSE` |
| [React Bits](https://github.com/DavidHDev/react-bits) · Orb, Strands | commit `e12c221` | MIT + Commons Clause（见 `manager/static/reactbits-LICENSE.md`） | `manager/static/reactbits-orb.js`, `reactbits-strands.js`（改写成不依赖 React 的函数 / rewritten without React） |
| [snd-lib](https://github.com/snd-lib/snd-lib) SND01 音效（土屋泰洋 / Yasuhiro Tsuchiya） | 1.2.4 | 代码 MIT；音频按 [snd.dev](https://snd.dev/) 条款免费使用，著作权归声音设计师 / code MIT; audio free to use under the snd.dev terms, copyright stays with the designer | `manager/static/sfx/`（已降调、滤波、剪辑 / pitch-shifted, filtered, trimmed；详见 `sfx/LICENSE.md`） |
| [Kenney Interface Sounds](https://kenney.nl/assets/interface-sounds) | — | CC0 | `manager/static/sfx/`（primary、done、warn） |

**React Bits 的限制 / React Bits restriction:** 可以作为应用的一部分使用和分发，但**不能把这两个组件单独拿出来出售、再授权或再分发**。
They may be used and distributed as part of an application, but **the components themselves may not be sold, sublicensed or redistributed on their own**.

**snd-lib 音效 / snd-lib sounds:** 条款禁止把未加工的原始音频单独再分发或出售；这里的文件都经过加工、只作为 Deck 的按键音使用。请不要把 `sfx/` 里的文件单独拿去分发。
The terms forbid redistributing or selling the unprocessed audio on its own; these files are processed and used only as Deck's UI sounds. Please don't redistribute the `sfx/` files on their own.

## 参考过写法的 / Approach borrowed from (no code copied)

| 项目 / Project | 许可 / License | 参考了什么 / What we borrowed |
|---|---|---|
| [Lockpaw](https://github.com/sorkila/lockpaw) | MIT | 拦截被系统停用时立刻重开、各种兜底 / re-enabling a disabled event tap, failsafes |
| [keylock](https://github.com/kfv/keylock) | MIT | HID 层 CGEventTap 吞按键 / swallowing keys with an HID-level CGEventTap |

## 安装时下载的 / Downloaded during setup (not in this repository)

| 组件 / Component | 许可 / License | 用途 / Used for |
|---|---|---|
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | Apache-2.0 | 唤醒、声纹、识别、VAD、Kokoro / wake word, voiceprint, ASR, VAD, Kokoro |
| KWS zipformer zh-en（sherpa-onnx） | Apache-2.0 | 唤醒词 / wake word |
| [3D-Speaker CAM++](https://github.com/modelscope/3D-Speaker) | Apache-2.0 | 声纹 / voiceprint |
| [Silero VAD](https://github.com/snakers4/silero-vad) | MIT | 判断说完 / end of speech |
| [Qwen3-ASR 0.6B](https://huggingface.co/Qwen) | 见模型页 / see model card | 识别定稿 / final transcript |
| [SenseVoice-Small](https://github.com/FunAudioLLM/SenseVoice) | 见模型页 / see model card | 备用识别 / fallback ASR |
| [mlx-audio](https://github.com/Blaizzy/mlx-audio) | MIT | 在 Apple GPU 上跑 TTS / TTS on the Apple GPU |
| [Qwen3-TTS 0.6B](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit) | 见模型页 / see model card | 语音合成 / speech synthesis |
| [Kokoro v1.1](https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models) | Apache-2.0 | 备用 TTS / fallback TTS |
| [pypinyin](https://github.com/mozillazg/python-pinyin) | MIT | 中文唤醒词 / Chinese wake words |
| [opencc-python-reimplemented](https://github.com/yichen0831/opencc-python) | Apache-2.0 | 繁转简 / Traditional → Simplified |
| [longbridge](https://pypi.org/project/longbridge/) | MIT / Apache-2.0 | 长桥行情（可选） / Longbridge quotes (optional) |

模型的许可以下载时模型页上写的为准。 / Model licenses are whatever the model card says when you download it.
