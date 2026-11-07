# 「你好Vesper」唤醒 + 声纹

| 部分 | 用的开源项目 | 版本 / 大小 | 许可 |
|---|---|---|---|
| 唤醒词 | [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) KWS `sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20`（官方最新中英模型，int8 chunk-16） | sherpa-onnx 2.0.1 | Apache-2.0 |
| 声纹 | [3D-Speaker](https://github.com/modelscope/3D-Speaker) CAM++ `3dspeaker_speech_campplus_sv_zh-cn_16k-common`（20 万中文说话人训练），经 sherpa-onnx 运行 | 约 27 MB | Apache-2.0 |
| 录入断句 | Silero VAD（和识别共用一份） | 约 0.6 MB | MIT |

为什么这样选：KWS 自定义唤醒词不用训练（拼音 token 直接写进 keywords 文件），整模型约 3M 参数、单线程实时率很低；CAM++ 是 3D-Speaker 里体积最小、中文效果接近最好（CN-Celeb EER 6.78%，ERes2NetV2 6.14% 但大 2.5 倍）的模型。思路参考了 py-xiaozhi 的中文唤醒配置。

## 怎么判断「是不是你」
1. KWS 听到「你好Vesper」（偏灵敏，门槛 0.25）。
2. 按 KWS 的 token 时间戳切出这一段，CAM++ 算声纹。
3. 分数 = 0.65 × 和你 5 遍唤醒词的相似度（取最像的 3 遍平均）+ 0.35 × 和你 3 句普通话的音色相似度。
4. 门槛 = 录入时留一法算出的你自己的平均分 − 0.10，限制在 0.55–0.75 之间。宁可偶尔叫不醒，也不让别人叫醒。
5. 高分唤醒（≥ 门槛 + 0.10）时自动补录，最多 30 条、10 分钟最多 1 条；原始 5 遍永远保留，防止越学越偏。

## 文件
- `wake.py` — 常驻唤醒（`start(hub)`），事件 `{"type":"wake","speaker_ok":bool,"score":float,"enrolled":bool}`。speaking 时、唤醒后 8 秒内、录入中不唤醒。
- `speaker.py` — 声纹档案和打分；`verify(samples)` 给识别线程核对一句话是不是你。
- `enroll.py` + `enroll.html` — 录入页 `GET /enroll`（8778），8 步：唤醒词正常×2、站远、小声、戴耳机（可跳过），3 句普通话。每遍都检查：有没有说出唤醒词、音量、爆音、和前几遍是不是同一个人。
- `wake_cli.py` — 独立调试：`live`（直接开麦 + 录入页在 8790）、`eval *.wav`、`bench`。
- `models/fetch_wake_models.sh` — 下载模型到 `~/Library/Application Support/Deck/voice/models/`。

## 数据
- 声纹：`~/Library/Application Support/Deck/voice/voiceprint/`（profile.npz、profile.json、录入原声 enroll/*.wav）。重新录入时旧档案整个移到废纸篓。
- 日志：`~/Library/Logs/Deck/friday-wake.jsonl`，只记分数不存音频，最多 2000 行。
- 调参：`~/Library/Application Support/Deck/voice/wake.json` 可覆盖 `kws_threshold`、`cooldown_s` 等（见 wake.py 的 DEFAULTS）。

## 2026-11-05 实测（Mac 内置麦克风，未经回声消除）
- 加载 0.6 s；KWS 单线程实时率 0.017；CAM++ 一次 27 ms；独立进程峰值内存约 230 MB（并入语音服务后共用 Python/onnxruntime）。
- 录入后留一法：0.47–0.69，门槛定 0.55。实际唤醒 4 次全过：0.688 / 0.704 / 0.737，延迟 220–420 ms。
- 冒充者：macOS 14 种中文合成声音说「你好Vesper」，最高 0.231，没有一个能叫醒；官方测试音频里的说话人最高 0.228。
- 注意：录入是直接用内置麦克风录的。正式走 DeckVoice.app（开回声消除）后若分数明显下降，在 Deck 设置里重新录一次即可。

## 2026-11-06 中英文分开录
- 录入加了 3 遍「Hey Friday」（可跳过）。中文、英文唤醒词各有一组录音、各自用留一法定门槛（中文下限 0.55，英文 0.50），英文唤醒只和英文那组比，自动补录也分开。
- 放宽线（soft_min_score 0.35 / soft_min_score_en 0.22）只给旧档案兜底：11-05 那份是内置麦克风直录、没经过 DeckVoice，而且没有英文。经 DeckVoice 重新录过（profile v2，chain=deckvoice）后，录过的语言不再放宽。
