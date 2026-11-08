# AI_SETUP · Install and configuration guide for your AI

[中文](AI_SETUP.md) · **English**

> **To the AI reading this:** your user wants Deck on their Mac. This repository holds **complete, runnable code** (butler service, dashboard page, voice, hands, native shell). Your job: check their Mac, ask what they want, then install and configure it step by step, **verifying each step before the next**. Talk in their language, avoid jargon, and ask for one thing at a time.
>
> Three hard rules: **never let them paste an API key into the chat** (use only the Keychain command in §3); **every "delete" is a move to the Trash**; **before wiring up any outside service, re-check it still works using [docs/services.en.md](docs/services.en.md)**. If a check fails, tell them and offer another option or skip it for now.
>
> The UI text is Chinese. If your user does not read Chinese, offer to translate the strings in `manager/dashboard.html`, `manager/news.html` and the assistant prompt in `manager/assistant.py`.

---

## 0. Ask and check first

| What you need to know | How |
|---|---|
| Mac model, chip, macOS version, free disk | Check: `sw_vers`, `sysctl -n machdep.cpu.brand_string`, `df -h ~`. **Apple silicon required**; voice needs macOS 14+; the system Liquid Glass assistant panel needs macOS 26+ (otherwise a web-drawn panel is used) |
| Python 3.11+, Xcode command line tools, Homebrew, gh | Check: `python3 --version`, `xcode-select -p`, `brew -v`, `gh auth status` |
| Where Deck should appear | Ask: a browser tab / an external screen or old iPad used as a display / dropped from the MacBook notch (needs the native shell, §6) |
| Which cards | Ask; default: Mac status + the AI tools they have (check `ls ~/.claude ~/.codex`, `which codex`) |
| Which LLM is the assistant's brain | Ask. Default DeepSeek (cheap, good Chinese). Others: §3.2 |
| Voice or not, and the assistant's name | Ask. Voice takes the most space (about 3 GB of models) and can be added later |
| News page, stocks | Ask. Stocks are implemented for **Longbridge Securities** only (see [docs/services.en.md](docs/services.en.md#longbridge-securities-market-data)); other brokers may not offer a usable open API |
| Let the assistant click on screen ("hands") | Ask. Needs the Accessibility permission; optional |

Keep the answers in a checklist and follow it.

## 1. Put the code in place

```bash
git clone https://github.com/3520566741-YCLi/deck-for-mac.git ~/deck-for-mac
cd ~/deck-for-mac/manager
cp config.example.json config.json        # local settings, already in .gitignore
cp apps.example.json apps.json            # apps the "My apps" card watches; edit to their answers
```

- `~/deck-for-mac` is the easiest place: the native shell and voice helper look there by default. Elsewhere works too; §6 shows how to point the shell at it.
- `config.json` never holds a key, only *where* to read keys from (§3).

## 2. Start the butler service (the core; do this first)

```bash
~/deck-for-mac/manager/launch.sh          # Python standard library only, no dependencies
open http://127.0.0.1:8777/dash
```

**Check:** the page opens and the CPU chart on the Mac status card moves every 2 seconds; `lsof -iTCP:8777 -sTCP:LISTEN` shows it listens on `127.0.0.1` only.

What each card needs:

| Card | Needs | Without it |
|---|---|---|
| Mac status / disk / daily cleanup / toolbox | Nothing | — |
| Claude Code | Claude Code installed. Quota left needs a status line command in `~/.claude/settings.json` (below) | The card says what is missing |
| Codex | Codex installed (`codex` command or the Codex app) | Same |
| GitHub | `brew install gh && gh auth login` (Deck stores no GitHub token) | Same |
| DeepSeek balance | A DeepSeek key (§3) | Same |
| News page | A DeepSeek key; stocks also need Longbridge authorization (§5) | Shows source headlines only |

Claude quota (let them run it, or edit with their consent after **backing up their settings.json**): add to `~/.claude/settings.json`

```json
"statusLine": { "type": "command", "command": "python3 ~/deck-for-mac/scripts/claude-statusline.py" }
```

To hide a card: set `dashboard.sources.<name>.enabled` to `false` in `config.json` and restart the service.

## 3. LLM and keys

### 3.1 The only way to store keys: the Keychain

Have them run this in **their own terminal** (it prompts; the key is not shown and not saved in history). **Do not ask them to send you the key.**

```bash
security add-generic-password -U -s deepseek-api -a "$USER" -w
```

| Service | Keychain service name | Used for | Required? |
|---|---|---|---|
| DeepSeek | `deepseek-api` | Assistant brain, news editor, cleanup review, process advice | Yes, for the assistant |
| TypeSafe Jev | `typesafe-api` | Picking the right button for "hands"; deciding whether speech was meant for the assistant | Optional (waitlisted early access; Deck works without it) |

Lookup order is `deepseek.key_source` in `config.json` (environment → Keychain → files). Check: `curl -s -o /dev/null -w "%{http_code}" https://api.deepseek.com/user/balance -H "Authorization: Bearer $(security find-generic-password -s deepseek-api -w)"` returns `200` (balance queries are free). **Run it yourself and never print the key.**

### 3.2 Pick a model (check first)

List the models this key can use; **trust the response, not names copied from docs**:

```bash
curl -s https://api.deepseek.com/models -H "Authorization: Bearer $(security find-generic-password -s deepseek-api -w)"
```

Then set in `config.json`:

| Key | Used by | Suggestion |
|---|---|---|
| `deepseek.model` | Assistant chat (needs streaming + tool calls) | The general chat model from the list; on 2026-11 that was `deepseek-flash` (older names like `deepseek-chat` may still be accepted; the list decides) |
| `news.model` / `news.fallback_model` | News page, three issues a day | The cheap flash tier |

Another provider: any API compatible with OpenAI Chat Completions that supports streaming and `tools`. Change `deepseek.base_url` and `deepseek.model`, store the key in the Keychain and update `key_source`. The balance card is DeepSeek-only; turn it off for other providers.

### 3.3 The assistant's name

The default is **Vesper** (tested with synthetic voices: about 72% wake rate, zero false wakes). To rename:

1. Pick an **uncommon two- or three-syllable** English name. `manager/voice/service/wake_cli.py` has tools to measure hit rate and false wakes with the system's synthetic voices; test before deciding.
2. Write it to `~/Library/Application Support/Deck/voice/wake_name.txt` (one line) and restart the voice service.
3. Display name: search the repo for `Vesper` and replace it (`manager/dashboard.html`, `manager/assistant.py`, `manager/news.html`, `news.title` in `config.json`).
4. Re-record the voiceprint with the new name at <http://127.0.0.1:8778/enroll>.

## 4. Voice (optional, the biggest part)

Everything runs locally: wake word, voiceprint, recognition and synthesis are never uploaded; only the recognized text goes to the LLM. About 2–3 seconds from the end of speech to the first word; see [docs/voice-latency.en.md](docs/voice-latency.en.md).

```bash
cd ~/deck-for-mac/manager/voice
service/models/fetch_wake_models.sh       # wake word + voiceprint models (tens of MB)
service/fetch_asr_models.sh               # recognition: Qwen3-ASR 0.6B (~940 MB) + SenseVoice (~230 MB) + VAD
helper/build.sh                           # build DeckVoice.app into ~/Applications
open ~/Applications/DeckVoice.app         # asks for microphone and speech recognition permission the first time
```

- DeckVoice starts the voice service itself (`service/run.sh` creates a venv at `~/Library/Application Support/Deck/voice/venv` and installs `requirements.txt` the first time).
- **Speech synthesis**: the default is Qwen's built-in voice (Qwen3-TTS 0.6B on the Apple GPU, about 1.9 GB). Install after DeckVoice has run once: `"$HOME/Library/Application Support/Deck/voice/venv/bin/pip" install -r service/requirements-tts.txt && service/tools/fetch_tts_models.sh`. For a smaller CPU fallback, download `kokoro-multi-lang-v1_1` from [sherpa-onnx's tts-models release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models) into `~/Library/Application Support/Deck/voice/models/` and write `{"voice": "kokoro:zf_001"}` to `~/Library/Application Support/Deck/voice/tts.json`.
- **Voiceprint**: open <http://127.0.0.1:8778/enroll> and read 16 prompts (about 4 minutes). After that only their voice can wake or interrupt it.
- Shortcuts: ⌥⌘J talk, ⌥⌘. stop, ⌥⌘M microphone on/off.

**Signing matters:** sign the three small apps (Deck, DeckVoice, DeckHands) with **their own Apple Development certificate** (Xcode → Settings → Accounts; a free Apple ID is enough). Without it the build falls back to ad-hoc signing and **microphone / Accessibility permissions must be granted again after every rebuild**.

**Check:** say "Hey Vesper" → a chime → ask "Is my Mac busy?" → it answers within 3 seconds; "stop" halts it within 0.3 s; someone else's voice cannot wake it.

## 5. News page and stocks (optional)

- The news page builds three issues a day in DeepSeek's off-peak hours (`news.slots` in `config.json`) and stops when the day's spend reaches `news.daily_budget_cny` (default ¥1; about ¥0.4 a day in practice). Edit `news.interests` to what they care about.
- All sources are free and public (RSS, Hacker News, GitHub Trending, arXiv, Hugging Face). **Spot-check them with [docs/services.en.md](docs/services.en.md) first**; replace dead ones in `news.feeds`.
- **Stocks (Longbridge Securities)**: they need their own Longbridge account with OpenAPI enabled. In the Invest section tap "Connect Longbridge"; Deck installs the official SDK into `~/Library/Application Support/Deck/news/lb-venv` and opens the OAuth page. **Tick only "Watchlist"; never any trading permission.** Other brokers: first check they offer a public market-data / watchlist API with read-only authorization; if not, leave the Invest section as news only.

## 6. Native shell Deck.app (external screen / notch, optional)

```bash
cd ~/deck-for-mac/app && ./build.sh        # SwiftPM build, sign, install to /Applications
open /Applications/Deck.app
```

- With an external screen it fills that screen; without one it drops from the notch (⌥Space). Menu bar icon → Display mode switches between them.
- If the code is not in `~/deck-for-mac/manager`: `defaults write io.github.deckformac.deck butlerPath <path to the manager folder>`.
- Deck.app starts the butler service. **After changing Python code**: quit Deck → `lsof -ti tcp:8777 | xargs kill` → open Deck again (quitting Deck alone can leave the old service running).

## 7. Hands (optional)

```bash
cd ~/deck-for-mac/manager/hands && ./build.sh   # DeckHands.app; asks for Accessibility permission on first use
```

The LLM decides what to do; DeckHands clicks. Deleting, sending, paying, quitting apps **always show a confirmation card first** and run only after they tap it; "stop" aborts at once; it never types into password fields.

## 8. Before you say "done"

- **Security**: all three ports (8777/8778/8779) listen on 127.0.0.1 only; requests without the token, with a wrong Host or a foreign Origin are rejected; `git status` shows no `config.json`, keys, voiceprints or `*.db`.
- **Stability**: restarting the service while the page is open reconnects by itself; unplugging the screen or switching microphones keeps working; data keeps refreshing after sleep.
- **UI**: look at every card in light and dark mode: no clipped text, nothing touching edges, nothing overlapping. You can run `node scripts/deck-ui-audit.js /tmp/deck-audit soft 1024x768 noshots` (needs Playwright).
- Tell them: all data lives in `~/Library/Application Support/Deck/`. To uninstall, quit the three apps, move that folder and `~/deck-for-mac` to the Trash, and delete `deepseek-api` from the Keychain.

## 9. Architecture at a glance

```
┌──────────── Their Mac (every service on 127.0.0.1 only) ────────────┐
│ Deck.app / browser ──HTTP+token──▶ butler :8777 (manager/server.py)   │
│        │                          ├─ monitor/     card collectors     │
│        │                          ├─ assistant.py chat + memory       │──▶ LLM (cloud)
│        │                          ├─ newsroom/    news page           │──▶ free sources / Longbridge
│        │                          └─ hands.py ──▶ DeckHands.app       │
│        └──SSE──▶ voice :8778 ◀─TCP :8779─ DeckVoice.app (mic/speaker) │
└───────────────────────────────────────────────────────────────────────┘
```

One brain: the ears only listen, the mouth only speaks, the hands only act, and only the LLM decides; one conversation turn at a time. Text from web pages, the screen, file names and news is **data, never instructions**. More: `manager/voice/ORCHESTRATION.md`, `manager/voice/INTERFACE.md` (Chinese).

## 10. Updating to a new version

Do this when the user says "update Deck". **Old versions are never deleted**: every version stays on GitHub, so you can go back to the previous tag if something breaks.

```bash
cd ~/deck-for-mac
git status --short                 # check for files they changed themselves; if any, ask before overwriting
git fetch --tags origin
git pull --ff-only origin main     # or pin a version: git checkout v0.1.1
```

- Installed from a zip, not git: unzip the new one elsewhere and **replace only the code folders** (`manager/`, `app/`, `scripts/`, `docs/`), keeping `manager/config.json` and `manager/apps.json`. Move the old folder to the Trash; don't delete it outright.
- Read the new version's section in `CHANGELOG.md` and tell the user what changed and whether anything extra is needed (for example a new optional component).
- Restart so the new code runs: quit Deck.app → stop the `server.py` on port 8777 → open Deck again (or `manager/launch.sh`). If voice code changed, quit and reopen DeckVoice.
- If `app/` changed, run `app/build.sh` again; if `manager/voice/helper/` or `manager/hands/` changed, run their `build.sh`.
- Check: `http://127.0.0.1:8777/dash` opens; with voice, `curl -s 'http://127.0.0.1:8778/wake/health?run=1'` reports `"ok": true`.

## Checklist

- [ ] `http://127.0.0.1:8777/dash` opens and Mac status updates every 2 seconds.
- [ ] Every card they chose shows real data or clearly says "missing X, run Y".
- [ ] Keys are only in the Keychain; `config.json` and `apps.json` are not committed.
- [ ] The assistant answers questions about card data and can open cards.
- [ ] (Voice) wake → ask → spoken answer within 3 s; "stop" works; others cannot wake it.
- [ ] (Stocks) the Longbridge grant has "Watchlist" only.
- [ ] (Hands) risky steps ask first.
