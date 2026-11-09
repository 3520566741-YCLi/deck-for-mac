# Deck 语音层

接口约定见 [INTERFACE.md](INTERFACE.md)。这里说怎么装、怎么调。

## 组成
| 部分 | 位置 | 干什么 |
|---|---|---|
| DeckVoice.app | `helper/`（Swift） | 菜单栏小助手。唯一占用麦克风（回声消除），Apple 本机实时字幕，播放 TTS，全局快捷键，拉起并守护 Python 服务 |
| 语音服务 | `service/`（Python 3.11 + sherpa-onnx 2.0.1） | 127.0.0.1:8778 给页面（SSE），127.0.0.1:8779 给 DeckVoice |
| 页面客户端 | `../static/deck-voice.js` | `window.DeckVoice`，只做数据，不画界面 |

## 识别怎么做到准（语音识别线程）
1. **边说边出字**：Apple `SFSpeechRecognizer` 中文本机识别，热词通过 `contextualStrings` 传进去。
2. **说完定稿**：silero VAD 判断句尾（停顿 0.7 秒），整句交给阿里的模型重新识别，这一版才发给 DeepSeek：
   - `qwen3`：Qwen3-ASR-0.6B int8（通义千问 ASR，中英混说强，**原生热词**）
   - `sensevoice`：SenseVoice-Small int8（FunAudioLLM，最快最省，热词靠拼音同音替换 + 纠错表）
   - 模型首次用到才加载，空闲 5 分钟卸载。唤醒时就开始预加载，等你说完已经就绪。
3. **热词**：`service/hotwords.txt` + `manager/apps.json` 里的应用名 + 你自己的 `~/Library/Application Support/Deck/voice/hotwords.txt`。英文热词还会自动纠正大小写和空格（deep seek → DeepSeek）。
4. **纠错表**：`service/corrections.txt`（克劳德 → Claude 这类），个人补充写在 `~/Library/Application Support/Deck/voice/corrections.txt`。改完 `curl -X POST 127.0.0.1:8778/asr/reload`。
5. **「停」**：助手在想或在说时，sherpa-onnx 关键词检测（和唤醒共用 KWS 模型）听到「停 / 停下 / 别说了 / 闭嘴 / stop」立刻清空播放、页面中止 DeepSeek 请求。听你说话时整句只说「停」也算。
6. **接着说**：助手说完后 6 秒内可以直接接话，不用再喊唤醒词。

## 快捷键（全局，不需要额外权限）
- **按住右 ⌘**（外接键盘，菜单里可换成右 ⌥；要「输入监控」权限）或 ⌃⌥⌘J：直接说，不用唤醒词。**按住说、松手马上出结果**；轻按一下则说完自动结束（再轻按取消）
- **轻按右 ⌥** 或 ⌃⌥⌘.：停（F5/F6 默认关，defaults write io.github.deckformac.deckvoice fKeys -bool true 可开）
- ⌃⌥⌘M 麦克风开/关（关掉后整个输入都不碰，菜单栏没有橙点）
- 菜单里可以另加「按住右侧 ⌥ / ⌘ 说话」（要输入监控权限）
- 别的 App（Claude 语音输入、会议、录音）在用麦克风时，DeckVoice 自动让出，用完再接回

## 安装
```bash
cd ~/deck-for-mac/manager/voice
service/fetch_asr_models.sh          # 识别模型（Qwen3-ASR ≈940MB + SenseVoice ≈230MB + VAD），只下一次
helper/build.sh                      # 编译 DeckVoice.app 装到 ~/Applications
open ~/Applications/DeckVoice.app    # 第一次会问麦克风和语音识别权限
```
DeckVoice 会自己跑 `service/run.sh`（第一次会建 venv、装依赖）。日志：`~/Library/Logs/DeckVoice-service.log`。

## 可调参数
写在 `~/Library/Application Support/Deck/voice/config.json`，键见 `service/config.py` 的 `DEFAULTS`。常用：
`asr_engine`（qwen3 / sensevoice）、`end_silence_s`（说完停顿多久算结束）、`followup_s`、`stop_phrases`、`speaker_gate`。
