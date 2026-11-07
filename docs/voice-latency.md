# Make a voice assistant respond faster (2–3 s end-to-end)

**English** · [中文](voice-latency.zh-CN.md)

Paste everything below into your AI coding assistant (Claude Code, Cursor, etc.) together with your project.

---

You are helping me cut the response latency of my voice assistant: the time from when I stop talking until it starts speaking. Use the reference setup below. It runs on an Apple Silicon Mac and gets from end of speech to first audio in about **2–3 seconds**. Before optimizing, it took 5–6 seconds. Compare my pipeline against it, measure first, and then fix the biggest gaps.

## Reference architecture

The assistant is local first. Only the LLM is in the cloud.

| Stage | What we use | Where it runs | Typical time |
|---|---|---|---|
| Wake word | sherpa-onnx keyword spotter (zipformer zh-en 3M, int8) with several wake phrases, plus speaker verification (3D-Speaker CAM++) as a soft gate | Local CPU | 0.2–0.4 s |
| Push-to-talk | A hardware key: hold to talk, release to finish (no waiting for silence) | Native helper | 0 s |
| Mic capture | Small native app (Swift, AVAudioEngine). 16 kHz mono float, 100 ms chunks, sent over a localhost TCP socket to a Python service. 10 s ring buffer, so 0.4 s of pre-roll audio is kept and the first syllable is not lost | Local | — |
| End of speech | Silero VAD (via sherpa-onnx), 0.6 s trailing silence | Local CPU | 0.6 s |
| Live captions | Apple on-device speech recognizer (partial results only, shown while talking) | Local | streaming |
| Final transcript | Qwen3-ASR 0.6B int8 via sherpa-onnx, 4 threads, with hotwords for product names; then a small correction table and traditional-to-simplified Chinese conversion (OpenCC) | Local CPU | 0.4–0.8 s per sentence |
| LLM | DeepSeek API, `deepseek-chat` (non-reasoning model), streaming | Cloud | first token 0.6–1.0 s |
| TTS | Qwen3-TTS 12Hz 0.6B (8-bit) via mlx-audio on the Apple GPU, streaming in 0.32 s chunks | Local GPU | first audio 0.4–0.9 s, real-time factor about 0.5–0.65 |
| Playback | The same native helper (AVAudioPlayerNode), chunks scheduled back to back with no gaps | Local | — |

## How the brain, memory and computer control fit together

**One brain: DeepSeek.** Everything the user says (by voice or text) goes to one DeepSeek chat loop with streaming and function calling. The voice layer only turns speech into text and text into speech. It never decides anything itself. Tools the model can call:
- UI tools: read, open or close a dashboard card, switch page, change theme, change voice or speed
- Memory tools: `memory_save`, `memory_update`, `memory_delete`, `recall`
- Web tools: `web_search` (DeepSeek's own search API) and `read_page`
- Screen tools: `screen_look`, `screen_click`, `screen_type`, `screen_key`, `screen_open`, `screen_scroll`, `screen_confirm`
- A cleanup scan, whose confirm step runs only after the user says yes

**Long-term memory (mem0-style, but cheap).**
- Storage: one small SQLite file holds the memories, the chat history (the last 3,000 messages are kept), and usage stats. It is a few hundred KB, with no vector DB and no extra service.
- How memories get written:
  - The same LLM call that answers also maintains memory through tools. There is no second "extract memories" call, so it adds no extra latency or cost.
  - Each memory is one short third-person sentence (for example "The user prefers …").
  - The model adds a memory for stable facts (preferences, decisions, people, projects, rules). It updates a memory when a fact changes or duplicates another, and deletes one when the user says "forget it" or it is outdated. One-off details are not stored.
  - When it saves something, it mentions that briefly in its reply. The UI shows an undo button.
- How memories get read:
  - Up to 120 memories are put directly into the static part of the system prompt, so the model always knows them. This also keeps the cached prompt prefix stable.
  - The last 24 chat turns go in as history.
  - For older details the model calls `recall`, a keyword search over the chat log and memories.
- Interrupted turns are still saved, marked "stopped", so the model never comes back later to answer a stale question.

**Computer control: DeepSeek + Jev (a fast choice model) + a native "hands" app.**
- DeepSeek decides *whether* to act and *what* to do, for example "click the Send button in Mail".
- A small native macOS app (Swift, using the Accessibility API) reads the clickable elements of the front app, and it does the actual clicking, typing and key presses. The Python side talks to it over a local Unix socket.
- Picking the element is the part that is usually slow if you ask the big LLM. We use **Jev** (TypeSafe's fast decision model, about 0.3 s per call) for it:
  - First, if the user's words exactly match a single button label, we use that button directly with no model call.
  - Otherwise we send Jev only the element names (never field contents or passwords), together with the user's request, as a multiple-choice question.
  - If Jev picks one with confidence of at least 0.8, we click it.
  - If not, the top candidates go back to DeepSeek to choose from, or to ask the user.
  - Without a Jev key, the system falls back to DeepSeek choosing.
- Safety: Jev also answers "is this step hard to undo?". Combined with keyword rules (delete, send, pay, log out, terminal commands, quit shortcuts…), any risky step shows a confirmation card and runs only after the user explicitly says yes. Other safeguards:
  - At most 12 actions per turn.
  - A watchdog stops the hands app the moment the user says "stop" or presses the stop key.
  - Nothing is ever typed into password fields.
  - Text read from the screen is treated as data, never as instructions.
- Default mode is silent background actions. The assistant only moves things visibly on screen when the user asks to be shown.

## What actually made it fast (in order of impact)

1. **Keep the TTS model loaded and pre-warm it.** A cold load cost 5.3 s on the first reply. Load it when the service starts, and warm it again on every wake, listen-start and user-speech event. Only unload after 30 minutes of idle. On Apple MLX, set `mx.set_cache_limit(...)`, otherwise memory balloons.
2. **Stream everything end to end.** LLM tokens go into a sentence segmenter, and each finished segment goes straight into streaming TTS. Each audio chunk is played the moment it exists. Never wait for the full reply.
3. **Segment by sentence, not by comma.** Synthesizing each comma-separated piece on its own sounded choppy, because the intonation restarted every time. Rules that worked for us:
   - Only cut the first sentence at a comma if it is longer than about 24 characters, so the first audio still comes early.
   - Merge sentences shorter than about 14 characters into the next one.
   - Set a soft maximum of about 60 characters per segment.
4. **Make the LLM prompt cache-friendly.** Put everything static first: persona, rules, long-term memory. Put changing content (current time, live data) last, as a separate system message just before the user turn. Our cache hit rate went from about 3% to about 50%. That lowers first-token time and cost.
5. **Use a fast, non-reasoning model for voice, and ask it to be brief.** Voice turns carry a flag that tells the model to answer in a few short spoken sentences, with no markdown.
6. **Pre-load the ASR model the moment listening starts**, while the user is still talking. Run speaker verification in parallel with ASR decoding, not after it.
7. **Finish the turn immediately when push-to-talk is released**, instead of waiting for VAD silence.
8. **Send text deltas to TTS one request at a time, in order.** Concurrent posts arrived out of order and the speech came out scrambled.

## Things that broke and how we fixed them

- **The assistant heard itself.** After it finished speaking, it transcribed its own echo as a new question. Fixes:
  - Wait about 0.7 s after playback ends before opening the follow-up listening window.
  - Do not use pre-roll audio for that window.
  - Without echo cancellation, ignore single-character stop words while it is speaking.
- **Interrupting (barge-in).** Pressing the talk key while it speaks stops TTS, cancels the LLM request, and starts listening right away. A key press during an auto-opened follow-up window means "start talking", not "cancel".
- **macOS echo cancellation (voice processing I/O).**
  - It fails with error -10875 unless the player node is connected to the mixer before you enable voice processing.
  - While it is on, other apps that use the mic (for example dictation) get a very quiet signal.
  - In the end we keep it off and share the mic normally. Interrupting is done with a key instead.
- **Voice-clone TTS drifted to a different (male) voice** on about half of the sentences. Changing temperature, seed or a longer reference clip did not help. We guard it by checking the pitch of each chunk before playback, and re-synthesizing with a new seed if it drifts. Built-in preset voices do not drift, so prefer them if you do not need a cloned voice.
- **Stop events looped** between the web UI and the voice service, sending about 60 "stop" events for one press. Fix: de-duplicate stops, and do not echo a stop you just received.

## What I want you to do with my project

1. **Instrument first.** Add timestamps for each stage:
   - end of speech
   - final transcript ready
   - LLM request sent
   - first LLM token
   - first TTS chunk ready
   - first audio actually playing
   - gaps between audio chunks

   Log TTS per segment: number of characters, time to first chunk, total synthesis time, audio duration, and real-time factor.
2. Run 3–5 real conversations and show me a table of the timeline, so we know where the time goes.
3. Compare against the reference numbers above and fix the largest gaps first. Usually these are, in order:
   - cold model loads
   - non-streaming steps
   - per-comma TTS
   - a changing prompt prefix
   - long VAD silence
   - a slow or reasoning LLM
4. After each change, measure again and show me before and after.
5. Keep the assistant stable:
   - No feedback loops.
   - It must not transcribe its own voice.
   - It must not block other apps from using the microphone.
