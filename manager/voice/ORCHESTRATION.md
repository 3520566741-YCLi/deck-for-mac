# Deck 里几个 AI 怎么配合（定稿 v1，11-05）

用户 的要求：Deck 里的几个 AI 彼此之间要协调。这份是唯一的规则，加新 AI（比如 Jev）之前先改这里。
接口细节见 [INTERFACE.md](INTERFACE.md)。

## 1. 谁负责什么（每个只干一件事）

| AI | 在哪 | 只负责 | 不许做 |
|---|---|---|---|
| 唤醒 + 声纹（sherpa-onnx KWS + CAM++） | `service/wake.py` `speaker.py` | 判断「是不是用户在叫 Vesper」→ 发 `wake` | 不碰扬声器，不决定做什么 |
| 语音识别（Qwen3-ASR，SenseVoice 后备，Apple 实时字幕） | `service/asr.py` | 把一句话变成文字 → 发 `user.final` | 不决定做什么，不调 DeepSeek |
| **Vesper = DeepSeek** | `manager/assistant.py`（`/api/chat`） | **唯一的大脑**：决定回答什么、调哪个工具 | 改 Mac 的操作必须先出确认卡；只在用户发起时才调用（不 24 小时跑） |
| 语音合成（Qwen3-TTS） | `service/tts.py` | 只念当前这一轮 Vesper 的回答 | 不自己说话，不跨轮念 |
| Jev（电脑操作，计划中） | Vesper 的一个工具 | 用户说「展示给我」时，替 Vesper 动手操作 | 不自己发起；一次只做一件；不碰麦克风和扬声器 |
| 进程建议 / 清理扫描 | `procs.py` `cleaner.py` | 用户点开卡片或开口时，让 DeepSeek 看一次 | 不常驻，不自动执行 |

**一句话：耳朵（唤醒、识别）只听，嘴（TTS）只说，手（Jev）只做，只有大脑（Vesper）做决定。**

## 2. 一个状态机，一次只有一轮

状态只在 hub（`service/hub.py`）里，页面和各模块都看它：

```
idle ──wake / ⌃⌥⌘J / 点麦克风──▶ listening ──user.final──▶ thinking ──第一段回答──▶ speaking ──念完──▶ idle
                                                     │                                   ▲
                                                     └──Vesper 调 Jev──▶ acting ──做完───┘
```

- 同一时间只有一轮对话。页面用 `chat.busy` 保证 `/api/chat` 不并发；语音和打字走同一个入口。
- 新的一轮来了（又说话、又打字）而上一轮还没完：**先停掉上一轮，再开始新的一轮**，不排队、不叠着说。
- `thinking` 超过 60 秒页面没接，hub 自己回 `idle`（asr.py 已做）。

## 3. 麦克风和扬声器只有一个主人

- **DeckVoice.app 是唯一碰麦克风和扬声器的进程。** 共享模式打开，别的软件要用麦克风时 DeckVoice 让出来（11-05 修过抢麦）。
- TTS 的声音只经 DeckVoice 播放（同一个音频引擎，回声消除才干净）。
- Vesper 在说话时，只认「停」这类打断词；追问窗口要等回声散掉才开（识别线程 97e5e0c）。
- Jev 和其它模块都不许自己开麦或放声音。

## 4. 打断只有一条路

「停」（语音）/ ⌃⌥⌘. / 页面上的停止按钮 → `hub.stop()` → 发 `stop` 事件，所有人照做：
- 页面：中止 `/api/chat`（AbortController），清掉还没发出去的回答文字（`deck-voice.js`）。
- TTS：作废当前这一轮（epoch +1），清合成队列；DeckVoice 清播放队列。
- Jev：取消当前操作，停在安全的位置，汇报做到了哪一步。
- 状态回 `idle`。

## 5. 改 Mac 的事都要你点头

清理、结束进程、Jev 动手、改设置：Vesper 先出确认卡（或说出来问你），你点了或说「好」才做。
清理只移到废纸篓；系统进程和 Deck 自己要用的进程（管家服务、语音服务、Deck、扩展屏程序、Claude）不能结束。

## 6. 日志放在一个地方

- 现在：管家服务 `manager/server.log`，语音服务 `[asr] [tts] [wake]` 打在它自己的日志里。
- 目标：都写到 `~/Library/Logs/Deck/`（`butler.log`、`voice.log`），每行带同一个**轮次号**，一轮从唤醒到念完能串起来看。
- 每轮要能看到：识别用时、DeepSeek 首字、TTS「第一段文字到出声」、每段合成实时率。

## 6.5 资讯页的 Vesper 早报（11-07）

- 早报口播稿由资讯页编排时 DeepSeek 顺手写好（同一次调用），不额外问 Vesper。
- 只在用户点头版底部的播放键时，资讯页调 `parent.DeckVoice.say(全文, true)` 念出来；不会自己开口。
- 「停」照常走第 4 条：`DeckVoice.stop()`，资讯页的播放条跟着停。

## 7. 还要补的（按顺序）

1. 轮次号：`user.final` 带 `turn`，`/api/chat`、`/assistant`、TTS、Jev 都带上同一个号；旧轮次的东西一律丢掉。
2. 打字时如果语音轮还没完，先 `stop` 再发。
3. Jev 接进来时加 `acting` 状态和 `jev.*` 事件，只能由 Vesper 的工具调用触发。
4. 日志合到 `~/Library/Logs/Deck/`。
5. 最终交付前做安全和稳定性审查，这份规则逐条核对。
