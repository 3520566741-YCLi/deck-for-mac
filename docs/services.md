# 外部服务清单和核查方法

**中文** · [English](services.en.md)

Deck 尽量都在本机跑，但有几样要用到外面的服务或模型。**每一项在接入前都要现场核查一遍**：服务会改名、涨价、下线，模型会改地址。下表是 **2026-11-07** 核查时的情况，只当参考；AI 给用户装的时候请按「怎么核查」那一列重新确认，**核查不通过就别接，告诉用户并给替代方案**。

## 一览

| 服务 | 做什么 | 必需？ | 费用 | 核查结果（2026-11-07） | 怎么核查 |
|---|---|---|---|---|---|
| [DeepSeek API](https://api-docs.deepseek.com/) | 助手大脑、资讯编辑、清理核对 | 用助手就要 | 按量付费；非高峰时段有优惠（以价格页为准） | 可用。文档当前列出的模型 ID 是 `deepseek-flash`、`deepseek-v4-pro`；旧名仍被接受。`GET /user/balance` 查余额 | `GET https://api.deepseek.com/models` 看这把 key 能用哪些模型；`GET /user/balance` 返回 200 |
| GitHub CLI `gh` | GitHub 卡片 | 可选 | 免费 | 可用 | `gh auth status` |
| Claude Code 状态栏 | Claude 剩余额度 | 可选 | 免费 | 可用：状态栏命令的输入里带 `rate_limits.five_hour / seven_day` | 装好 `scripts/claude-statusline.py` 后看 `~/deck-for-mac/logs/claude-rate-limits.json` 有没有生成 |
| Codex `app-server` | Codex 卡片 | 可选 | 免费 | 可用 | `codex app-server --help` |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 2.0.1 + 模型 | 唤醒、声纹、识别、VAD | 语音要 | 免费，Apache-2.0 | 可用：脚本里的下载地址都在 GitHub 发布页 | 跑 `fetch_wake_models.sh`、`fetch_asr_models.sh` 不报 404 |
| [Qwen3-ASR 0.6B（sherpa-onnx 版）](https://k2-fsa.github.io/sherpa/onnx/qwen3-asr/pretrained.html) | 识别定稿 | 语音要 | 免费 | 可用 | 同上 |
| [mlx-audio](https://github.com/Blaizzy/mlx-audio) + [Qwen3-TTS 0.6B](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit) | 语音合成 | 语音要（可换 Kokoro） | 免费 | 可用（需 Apple 芯片） | `fetch_tts_models.sh` 能下完 |
| [TypeSafe Jev](https://docs.typesafe.ai/) | 动手时挑按钮、打断时判断 | **可选** | 很便宜 | **公测排队中**，不一定拿得到 key。没有 key 时 Deck 自动跳过这一步 | 用户有 key 才接；没有就不提 |
| 资讯来源（RSS、Hacker News、GitHub Trending、arXiv、Hugging Face、Google News） | 资讯页原料 | 资讯页要 | 免费 | 大多可用；36 氪 /feed 会返回反爬页，OSS Insight 趋势接口已停 | 在用户的 Mac 上 `curl -sI` 每个地址，挑 200 的留下 |
| [长桥证券 OpenAPI](https://open.longbridge.com/docs/getting-started) | 投资分栏的行情和自选 | **可选** | 美股 LV1 行情 OpenAPI 免费；其他市场看权限 | 可用，详见下文 | 见下文 |

## DeepSeek

- 用钥匙串存 key：`security add-generic-password -U -s deepseek-api -a "$USER" -w`。
- 模型名**以 `/models` 的返回为准**。2026-11 文档写着：用 `deepseek-flash`，旧的 `deepseek-v4-flash` 等名字仍被接受、按 Flash 计费。
- 价格和优惠时段会变：去 [价格页](https://api-docs.deepseek.com/quick_start/pricing) 看当前的，再决定资讯页 `news.slots` 排在几点。Deck 默认三期（06:30、12:30、20:00）都排在 2026-11 时的非高峰时段，每天封顶 `news.daily_budget_cny`。
- 助手每轮对话的输入大约 8k token（卡片摘要 + 记忆 + 历史），大部分会命中缓存。

## 长桥证券（行情）

Deck 的投资分栏以**长桥证券**为例实现。其他券商不一定提供能个人使用的行情 / 自选开放接口，接之前要单独查。

**谁能用：** 要有长桥证券账户，并在 [开发者中心](https://open.longbridge.com/) 开通 OpenAPI。账户分数据中心：美国账户只能走 `.com` 域名；新加坡 / 香港账户 `.com`、`.cn` 都能用；部分接口只给新加坡 / 香港账户。

**授权：** 官方推荐 OAuth 2.0。Deck 注册客户端时**只申请 `watchlist`（自选）权限**，看行情本身不需要额外权限。**绝不申请** `account.read`、`trade.read`、`trade.write`。旧的 App Key + Access Token 方式能下单，Deck 不用它。令牌由官方 SDK 存在 `~/.longbridge/openapi/tokens/<client_id>`，Deck 不复制、不上传。

**行情权限：** 在开发者中心看「行情权限」。美股 LV1（仅 OpenAPI）免费；港股基础报价没有实时推送；其他市场（港股实时、A 股等）可能要在长桥 App「我的 → 我的行情 → 行情商城」购买。没有的权限 Deck 只显示资讯，不显示那个市场的实时价格。

**SDK：** 官方 Python 包 `longbridge`（原 `longport`，已改名），MIT / Apache-2.0 双许可，Python ≥ 3.8。Deck 把它装在单独的虚拟环境 `~/Library/Application Support/Deck/news/lb-venv`，不污染系统 Python。

**核查：** 用户在开发者中心能看到 OpenAPI 已开通 → 在 Deck 投资分栏点「连接长桥」→ 授权页上只勾「自选」→ 投资分栏出现自选股行情。授权页出现交易权限就停下，告诉用户。

**别的券商怎么办：** 先查三件事：①有没有面向个人的开放行情接口；②能不能只读授权（不给交易权限）；③你所在地区能不能用。三项都满足再仿照 `manager/newsroom/lb_helper.py` 写一个小助手；任何一项不满足，投资分栏就只放资讯，不接行情。

## 隐私

- 发到云端的只有：发给大模型的文字（你说的话、卡片摘要、记忆、资讯候选标题）。录音、声纹、文件内容、屏幕截图都不上传。
- 动手时发给 Jev 的只有屏幕上按钮的名字，不发输入框内容。
