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

## 2026-11-08 第二条唤醒路 + 自检
- 问题：KWS（3M 参数）对用户真人说的「你好 Vesper」常常压根不触发，没触发就连日志都没有，表现就是「叫了好多遍一点反应都没有」。
- 第二条路：Silero VAD 切出待机时的短句 → SenseVoice（226 MB int8，一句 40–60 ms）转文字 → 名字在句子开头（前面最多是「你好 / Hey / 早上好」这类短招呼）→ 声纹核对，和 KWS 同一个门槛。长句只认开头 2.4 秒；「你好…」开头没认出名字时再只认开头 1.7 秒。名字后面已经说的话（「你好 Vesper 现在几点」）用 preroll 交给识别，不用再说一遍。
- 名字的写法见 `wake.match_name`：WSPER / WESPER / VISSPER / WEPER / WPER / WER / 威PER / 维斯帕…；PAPER、SUPER、HELPER、WHISPER、JASPER、「我刚跟 Vesper 聊天」都不算。
- 声纹放宽线：门槛 − 0.13，不低于 0.42（他真人实测 0.42–0.68；32 种合成声音冒充最高 0.357）。
- 离线测：他 13 遍录音 × 2 种音量 26/26 叫醒；他的 3 句普通话、16 句含 paper/super/whisper/hello 的合成句子 0 次误唤醒；冒充 0 次通过。
- 日志新增：`path: "asr"`（第二条路唤醒）、`event: "heard"`（以招呼开头却没认出名字，只记文字）、`event: "health"`。
- 自检：每 10 分钟拿 enroll/w1.wav 跑两条路 + 看音频有没有进来、唤醒线程有没有卡住、DeckVoice 连没连上；坏了发 `voice.health` 事件，Deck 弹提醒（同一问题 30 分钟一次），语音服务连不上 1 分钟也提醒。`GET /wake/health?run=1` 马上测一次，`/status` 里有 `wake_health`。

## 2026-11-08 名字可以自己改（Deck 设置 → 助手）
- 起因：公开版的用户不叫 Vesper，要能自己起名字、自己录声纹、自己测。
- 改名：`POST /assist/name {name, hint}`（`assist.py`），写 `wake_name.txt`（第一行名字、第二行读法提示），`wake.rename()` 当场重建关键词和唤醒模型，建不起来就退回原名。名字只收 3–12 个英文字母或 2–4 个汉字，Siri / Alexa / 小爱这类不收。中文名字只配中文叫法，录入也没有英文那几步。
- 读法（`names.py`）：先查唤醒模型自带的 CMU 词典 `en.phone`；查不到的名字（Kaia、Mochi）用简化的字母发音规则猜 1–3 种读法（思路来自 NRL 字母发音规则，公有领域）；用户填的读法提示（汉字按拼音、英文按单词）排最前。名字还是 Vesper 时生成的关键词文件和改之前逐字一样。
- 第二条路认名字：名字和识别结果都换成「粗音标」（V/W/F、P/B、T/D… 合并，元音只分 5 类，中文按拼音），加权编辑距离 ≥ 0.8 算叫到（短名字要求更高）；词典里的常用词（WHISPER、WATER）要 ≥ 0.97。名字是 Vesper 时，按他真人录音调好的老写法照样先用。录声纹时 SenseVoice 把名字写成什么会记进 `name_aliases.json`，以后直接认。词典不整份读进内存（省 60 MB），只留原文按需查。
- 名字体检：`POST /assist/namecheck`，用 macOS 合成声音（中文 2 种、英文 3 种）把叫法说一遍、再混 28 句普通聊天，算两条路各认出几成、误认几次。只是参考（Vesper 合成只算第一条路约 72%）。
- 测试：`POST /assist/test/start {mode}`——me（自己叫 10 遍：唤醒率 + 声纹通过率）、other（别人叫 10 遍：被放行几次）、quiet（安静听 1–10 分钟：误醒几次）。测试时结果只交给测试、不真的唤醒，**绝不拿测试时的声音补录声纹**；最长 12 分钟自动结束，录入时不能测。
- 录入：Deck 里的引导录入用的还是 `/enroll/api/*`。录入时唤醒暂停，所以网页关掉没退出时 15 分钟自动收尾。
