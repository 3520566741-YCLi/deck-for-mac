# Deck 语音层接口（定稿 v1）

> 采用语音识别线程起草的方案（单一麦克风 + 回声消除，播报时也能听到「停」）。
> 文字对话线程负责整合，补充了下面「整合约定」。这份是唯一权威版本；改接口先改这里。

## 整合约定（文字对话线程补充）
- 路径：Swift 小助手在 `manager/voice/helper/`，Python 服务在 `manager/voice/service/`。各线程只改自己名下的文件。
- 启动：Deck.app 启动时执行 `open -g -a ~/Applications/DeckVoice.app`（文字对话线程改 Deck）；DeckVoice 负责拉起并守护 Python 服务（`manager/voice/service/run.sh`，可重复执行，端口已占用就退出 0）。所以麦克风/语音识别权限归 DeckVoice，用 Apple Development 签名，不要 ad-hoc。
- 内存：一个 Python 进程。ASR（SenseVoice）、TTS（Kokoro）首次用到才加载，空闲 5 分钟卸载；常驻只有唤醒 + 声纹。模型放 `~/Library/Application Support/Deck/voice/models/<模型名>/`，venv 放 `~/Library/Application Support/Deck/voice/venv`（三个语音线程已按这个对齐）。
- 页面接入：识别线程提供 `manager/static/deck-voice.js`（只做客户端库，不画界面），暴露
  `window.DeckVoice = { on(type, cb), mic(on), listen(), stop(), say(text, done), status() }`，内部用 EventSource 连 8778。
  字幕、对话气泡、边缘光都由文字对话线程画在 Deck 的对话面板里。`server.py` 只负责把 `/static/deck-voice.js` 原样发出去。
- 8778 的 CORS 只允许 `http://127.0.0.1:8777` 和 `http://localhost:8777`；只听 127.0.0.1。
- 声纹录入：唤醒线程写 `service/enroll.py` + 录入页面，由 service 的 HTTP 挂在 `GET /voiceprint`（识别线程在 service/server.py 里留一行挂载）。Deck 设置里用 iframe 打开它。
- 11-08 起：Deck 设置 →「助手」直接调 `/enroll/api/*` 和 `/assist/*`（改名、名字体检、唤醒/声纹测试，见 `service/assist.py` 和 `service/WAKE.md`），不再用 iframe。录入时录音也走 helper 的音频（`hub.subscribe_audio`），不要另开麦。
- 唤醒后流程：`wake`（speaker_ok=true）→ hub 进入 listening，开 Apple 实时字幕 → 说完 → `user.final` → 页面调 `/api/chat` 问 Vesper → 页面把回答增量 `POST /assistant` → TTS 按句播报 → `assistant.done` 后回到 idle。`speaker_ok=false` 不进入 listening。
- 「停」：任何时候 `stop` → 页面中止 `/api/chat`（AbortController），helper 清空播放队列。

---


## 进程
1. **DeckVoice.app**（Swift，LSUIElement，~/Applications/DeckVoice.app，仓库 `voice/helper/`）
   - 唯一拥有麦克风的进程。AVAudioEngine，开 voice processing（回声消除），这样 TTS 在说话时也能听到「停」。
   - Apple SFSpeechRecognizer zh-CN 本机识别 → 实时字幕（只在 listening 状态跑）。
   - TTS 音频也由它播放（同一个 engine 的输出，AEC 才最干净）；收到 stop 立即清空播放队列。
   - 全局快捷键（Carbon RegisterEventHotKey，无需辅助功能权限）：⌥⌘M 麦克风开/关，⌥⌘J 不用唤醒词直接说，⌥⌘. 停。
   - 通过 TCP 127.0.0.1:8779 连 Python 语音服务，断线自动重连。
2. **deck-voice 服务**（Python 3.11 venv，`voice/service/`，sherpa-onnx）
   - 127.0.0.1:8779 收 helper 的音频和事件；127.0.0.1:8778 给 Deck 页面 SSE + HTTP。
   - 模块各归各线程，互不改文件：
     - `hub.py` / `server.py` / `asr.py` / `stopword.py` —— 语音识别线程
     - `wake.py` / `speaker.py` —— 唤醒+声纹线程
     - `tts.py` —— 语音合成线程

## helper ⇄ 服务 帧格式（TCP 8779）
每帧：1 字节类型 + 4 字节大端长度 + 负载
- `A` helper→服务：16 kHz 单声道 float32 小端 PCM（约 100ms 一帧，已过回声消除）
- `J` 双向：UTF-8 JSON 一行
  - helper→服务：`{"t":"partial","text":...}` `{"t":"apple_final","text":...}` `{"t":"mic","on":bool}` `{"t":"hotkey","name":"mic"}` `{"t":"playback_done","id":...}`
  - 服务→helper：`{"t":"listen","on":bool,"hotwords":[...]}`（开/关 Apple 实时识别） `{"t":"mic","on":bool}` `{"t":"stop"}`（立刻停播放） `{"t":"play_mark","id":...}`（播到这里回 playback_done） `{"t":"vp","on":bool}`（临时开/关回声消除：她想和说的时候开，回到待机 2 秒后关） `{"t":"hold","on":bool}`（开口打断第一步：淡出并原地暂停 / 淡入接着说）
- `P` 服务→helper：要播放的 PCM（float32 小端，前 4 字节为采样率 uint32 LE，其后为数据）

## Python 内部（hub）
```python
from service import hub
hub.subscribe_audio(cb)          # cb(np.float32 array 16k)，唤醒/声纹用
hub.on(event_type, cb)           # 订阅事件
hub.emit({"type": ..., ...})     # 发事件（也会推到 SSE）
hub.set_state("idle"|"listening"|"thinking"|"speaking")
hub.play(pcm, sr, id=None)       # TTS 线程用：非阻塞交给 helper 播放；有 id 时播完发 playback_done
hub.route(method, prefix, fn)    # 挂 HTTP：fn(method, path, query, body) -> (status, bytes, ctype)
hub.recent_audio(seconds)        # 最近几秒麦克风音频
hub.stop()                       # 停：清播放 + 发 stop 事件（DeepSeek 端收到要取消生成）
```

## 事件（SSE `GET http://127.0.0.1:8778/events`，页面订阅）
| type | 谁发 | 字段 |
|---|---|---|
| `wake` | 唤醒线程 | `speaker_ok`, `score` |
| `state` | hub | `state`: idle/listening/thinking/speaking |
| `user.partial` | 识别 | `text`（Apple 实时） |
| `user.final` | 识别 | `text`（SenseVoice 定稿+热词纠正）, `apple`, `lang`, `emotion`, `ms` |
| `assistant.delta` / `assistant.done` | Deck 对话 | `text` |
| `stop` | 识别 | `source`: voice/hotkey/ui |
| `mic` | helper | `on` |
| `level` | hub / tts | `rms` 0-1，`src`: mic（listening 时）/ tts（speaking 时），约 15Hz，给 DeckGlow.set({level}) |
| `listen` | 识别 | `on`, `source`(wake/followup/hotkey/ui) 或 `reason`(utterance/timeout/stop) |
| `user.speaking` | 识别 | 听到他开口了（VAD） |
| `user.empty` / `user.rejected` | 识别 | 没听清 / 声纹不是 用户（speaker_gate 打开时） |
| `playback_done` | helper | `id` |
| `tts.segment` | tts | `text`（这一段清洗后的文字）, `in_ms`（再过多少毫秒开始出声）；资讯页早报用它逐句高亮、暂停后从这句继续 |
| `helper` | hub | `connected` |
| `hotkey` | helper | `name`: mic/talk/stop |

## Deck 对话（主线程）怎么接
- 订阅 `user.final` → 把 `text` 发给 DeepSeek；收到 `stop` 取消请求。
- 回答文字：`POST http://127.0.0.1:8778/assistant {"text":..., "done":bool}` → 推 `assistant.*` 事件并交给 TTS。
- 页面按钮：`POST /mic {"on":bool}`、`POST /stop`。

## 麦克风是否真的在送声音（11-06）

`GET /status` 多了 `audio`：最近 5 秒收到几块（`chunks_5s`，正常约 50）、多少秒（`seconds_5s`）、`rms_dbfs`、`peak`、
`last_ms_ago`（上一块离现在多久）、`restarts`（看门狗重开麦克风的次数）。
看门狗：麦克风开着却 4 秒没收到声音，或 8 秒里全是 0，就给 helper 发 `mic off → on` 重建音频引擎，
退避 10/30/60 秒；同时发 `audio.stall` 事件。换输入设备（蓝牙耳机、连续互通）后 helper 不再送声音时靠它自己恢复。

## DeckVoice 崩溃自动拉起（11-07）
服务发现 helper 断开超过 15 秒，就 `open -g -b io.github.deckformac.deckvoice` 把 DeckVoice 重新打开（不抢焦点），
退避 15/60/300 秒，一小时最多 5 次；发 `helper.relaunch` 事件，`/status` 的 `helper_relaunches_1h` 是这一小时拉了几次。
从菜单正常退出 DeckVoice 时它先发 `{"t":"quit"}`，服务记下后不再拉起（`/status` 的 `helper_quit`），直到 DeckVoice 自己再连上。
注意：DeckVoice 启动时如果 8778 已有服务在跑就直接复用，退出时不会关掉那份服务；换服务代码要单独 `pkill -f service.server`。
helper 侧：播放前确认引擎真在跑，`play()` / `installTap` 用 ObjC `@try` 包住（`helper/ObjC/DVTry`），不再因 NSException 崩溃；
蓝牙耳机（如 AirPods）、iPhone 连续互通不再被选作麦克风，默认输入是这类设备时改用 Mac 自带麦克风（此时不开系统回声消除）。

## 打断时先「让一下」（11-06）

服务 → helper：`{"t":"duck","on":true}` 把她的音量在约 80ms 内压到 40%（不停）；`on:false` 恢复。
她说话时一听到人声就 duck，攒够 0.8 秒核对声纹：是 用户 → `hold` 暂停（从压低后的音量淡出）；
明显不是他 → 立刻恢复；拿不准 → 攒到 2 秒再核对一次。暂停中 duck 被忽略。旧版 helper 不认 duck，只是没有这一步。

## 接话窗口 1 分钟（11-06）

她说完后 `followup_s`（60 秒）内直接开口就行。窗口里每句都核对声纹（门槛 max(0.45, 档案门槛 − 0.15)，不到 0.8 秒的短句算通过）：
不是他、是她自己的回声、没听出字 → 发 `user.rejected`（source=followup）并在剩下的时间里接着听；窗口到时发 `followup.end`，
提示音模块轻轻响一下关闭音（音量减半），之后要重新叫名字。日志 friday-barge.jsonl 记 `followup_voice`（分数、是否通过）。

## Windows 版（11-08）
- 没有 DeckVoice.app：小助手是 `windows/voice_helper.py`（sounddevice），和语音服务跑在同一个子进程 `Deck.exe --voice` 里
  （`windows/voice_launch.py`：Deck 窗口进程守护它，崩了重启，Deck 退出它跟着退出；8778 已有一份就不起第二份）。
- 帧格式不变。小助手连上先发 `{"t":"hello","platform":"win","aec":false,"captions":false,"hotkeys":{...}}`（服务推成 `helper.hello` 事件），
  再发 `{"t":"aec","on":false}`；收到 `vp` 也回 `aec:false`。没有 Apple 实时字幕：不发 `partial` / `apple_final`，`user.partial` 不会出现，定稿照常。
- 麦克风打不开时 `mic` 的 `reason` 多一种 `no_device`（没有麦克风 / 没有声卡）；`no_permission` = 系统设置里关了麦克风权限。
- 快捷键：Ctrl+Alt+M 麦克风、Ctrl+Alt+J 直接说（按住 = 按住说话）。「停」的 Ctrl+Alt+. 归电脑操控急停（winport/hands_win.py）。
- 路径：`%APPDATA%\Deck\voice`（`config.py` 的 `ROOT`，`DECK_VOICE_ROOT` 可改），日志 `%LOCALAPPDATA%\Deck\logs`（voice.log、friday-*.jsonl）。
- 朗读只有 Kokoro（`kokoro:zf_001`）；千问音色要 mlx，Windows 上显示未安装。缺模型时 `tts.error` 的 `error` 是「语音合成模型还没下载…」。
- 模型：`windows/voice_models.py` 从 k2-fsa/sherpa-onnx Releases 下载（续传 + 大小 + SHA-256），第一次启动自动下载 wake/asr/tts（约 375MB，
  `config.json` 里 `"auto_models": false` 关掉）。接口：`GET /models/status`、`POST /models/download {"packs":[...]}`、`POST /models/cancel`；
  事件 `models.progress`（item/label/phase/done/total/bytes_done/bytes_total）、`models.done`（ok/installed/error）、`models.missing`。
  新装了唤醒模型会在待机时自动重启一次语音服务。
