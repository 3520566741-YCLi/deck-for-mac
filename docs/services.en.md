# Outside services and how to verify them

[中文](services.md) · **English**

Deck runs on your Mac as much as possible, but a few parts use outside services or models. **Re-check every one before wiring it up**: services get renamed, repriced or shut down, and model URLs move. The table records what we found on **2026-11-07**; treat it as a reference. An AI installing Deck should re-verify using the "How to check" column, and **if a check fails, don't wire it up — tell the user and offer an alternative**.

## Overview

| Service | What for | Required? | Cost | Status on 2026-11-07 | How to check |
|---|---|---|---|---|---|
| [DeepSeek API](https://api-docs.deepseek.com/) | Assistant brain, news editor, cleanup review | For the assistant | Pay as you go; off-peak discount (see pricing page) | Working. The docs list model IDs `deepseek-flash` and `deepseek-v4-pro`; older names are still accepted. `GET /user/balance` for the balance | `GET https://api.deepseek.com/models` shows which models this key can use; `GET /user/balance` returns 200 |
| GitHub CLI `gh` | GitHub card | Optional | Free | Working | `gh auth status` |
| Claude Code status line | Claude quota left | Optional | Free | Working: the status line input carries `rate_limits.five_hour / seven_day` | After installing `scripts/claude-statusline.py`, `~/deck-for-mac/logs/claude-rate-limits.json` appears |
| Codex `app-server` | Codex card | Optional | Free | Working | `codex app-server --help` |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 2.0.1 + models | Wake word, voiceprint, recognition, VAD | For voice | Free, Apache-2.0 | Working: every download URL in the scripts is on the GitHub releases page | `fetch_wake_models.sh` and `fetch_asr_models.sh` finish without 404s |
| [Qwen3-ASR 0.6B (sherpa-onnx build)](https://k2-fsa.github.io/sherpa/onnx/qwen3-asr/pretrained.html) | Final transcript | For voice | Free | Working | Same |
| [mlx-audio](https://github.com/Blaizzy/mlx-audio) + [Qwen3-TTS 0.6B](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit) | Speech synthesis | For voice (or Kokoro) | Free | Working (Apple silicon) | `fetch_tts_models.sh` completes |
| [TypeSafe Jev](https://docs.typesafe.ai/) | Picking buttons for "hands"; judging interruptions | **Optional** | Very cheap | **Waitlisted early access**; a key may not be available. Deck skips this step without a key | Wire it up only if the user has a key |
| News sources (RSS, Hacker News, GitHub Trending, arXiv, Hugging Face, Google News) | News page input | For the news page | Free | Mostly working; 36Kr's /feed returns an anti-bot page and the OSS Insight trending API is gone | `curl -sI` each URL on the user's Mac and keep the 200s |
| [Longbridge OpenAPI](https://open.longbridge.com/docs/getting-started) | Quotes and watchlist in the Invest section | **Optional** | US LV1 quotes via OpenAPI are free; other markets depend on entitlements | Working, see below | See below |

## DeepSeek

- Store the key in the Keychain: `security add-generic-password -U -s deepseek-api -a "$USER" -w`.
- **Model names come from `/models`.** On 2026-11 the docs said to use `deepseek-flash`; older names such as `deepseek-v4-flash` are still accepted and billed at the Flash price.
- Prices and off-peak hours change: read the [pricing page](https://api-docs.deepseek.com/quick_start/pricing) before choosing `news.slots`. Deck's default three issues (06:30, 12:30, 20:00 Beijing time) were all off-peak in 2026-11, capped per day by `news.daily_budget_cny`.
- Each assistant turn sends about 8k input tokens (card summary + memory + history), most of which hit the cache.

## Longbridge Securities (market data)

Deck's Invest section is implemented with **Longbridge Securities** as the example. Other brokers may not offer an open market-data / watchlist API for individuals; check separately before wiring one up.

**Who can use it:** you need a Longbridge account and OpenAPI enabled in the [developer center](https://open.longbridge.com/). Accounts live in different data centers: US accounts must use the `.com` domains; Singapore / Hong Kong accounts can use `.com` or `.cn`; some endpoints are only available to Singapore / Hong Kong accounts.

**Authorization:** OAuth 2.0 is the recommended method. Deck registers its client asking **only for the `watchlist` scope**; market data needs no extra scope. It **never asks for** `account.read`, `trade.read` or `trade.write`. The legacy App Key + Access Token method can place orders, so Deck does not use it. The official SDK stores the token in `~/.longbridge/openapi/tokens/<client_id>`; Deck does not copy or upload it.

**Quote entitlements:** see "Quote authority" in the developer center. US LV1 (OpenAPI only) is free; the Hong Kong basic quote has no real-time push; other markets (real-time HK, A-shares, etc.) may need to be bought in the Longbridge app under Me → My Quotes → Store. Without an entitlement Deck shows news only, not live prices, for that market.

**SDK:** the official Python package `longbridge` (renamed from `longport`), dual-licensed MIT / Apache-2.0, Python ≥ 3.8. Deck installs it into its own venv at `~/Library/Application Support/Deck/news/lb-venv`, leaving the system Python alone.

**Check:** the developer center shows OpenAPI enabled → tap "Connect Longbridge" in Deck's Invest section → tick only "Watchlist" on the authorization page → watchlist quotes appear. If the page asks for trading permissions, stop and tell the user.

**Other brokers:** check three things first: (1) a public market-data API for individuals; (2) read-only authorization without trading rights; (3) availability in the user's region. Only if all three hold, write a small helper modeled on `manager/newsroom/lb_helper.py`; otherwise keep the Invest section to news only.

## Privacy

- The only things sent to the cloud are the texts sent to the LLM (what you said, card summaries, memories, candidate news headlines). Recordings, voiceprints, file contents and screenshots are never uploaded.
- For "hands", only the names of on-screen buttons go to Jev, never the contents of text fields.
