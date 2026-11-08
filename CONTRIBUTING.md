# 参与贡献 / Contributing

谢谢你愿意帮 Deck 变得更好！ / Thanks for helping make Deck better!

## 先开 issue / Open an issue first

- **报 bug**：用 [Bug 模板](../../issues/new?template=bug_report.yml)，写清 macOS 版本、芯片、哪张卡片或哪个模式、怎么复现。
  **Bugs:** use the [bug template](../../issues/new?template=bug_report.yml) with your macOS version, chip, which card or mode, and steps to reproduce.
- **想加功能**：先用 [功能模板](../../issues/new?template=feature_request.yml) 聊一聊，再动手写，免得白做。
  **Features:** start with the [feature template](../../issues/new?template=feature_request.yml) before writing code, so nobody's work is wasted.
- **安全问题**：不要开公开 issue，请看 [SECURITY.md](SECURITY.md)。 / **Security:** don't open a public issue; see [SECURITY.md](SECURITY.md).

## 提 PR / Pull requests

1. 一个 PR 只做一件事，标题写清做了什么。 / One change per PR, with a clear title.
2. 改了 Python，跑一遍语法检查：`python3 -m py_compile manager/*.py manager/voice/service/*.py`。
   If you touched Python, run `python3 -m py_compile manager/*.py manager/voice/service/*.py`.
3. 改了界面，附白天和夜里的截图；检查文字不贴边、不重叠、不被遮挡。
   If you touched the UI, attach day and night screenshots, and check nothing touches an edge, overlaps or gets clipped.
4. **截图和日志里不要带个人信息**（名字、路径、key、聊天记录）。 / **No personal data in screenshots or logs** (names, paths, keys, chats).
5. 新功能的截图出中英两版：中文图放 `docs/images/`，内容一模一样的英文图放 `docs/images/en/`（同名）。新加的界面文字同时在英文词典里补一行。
   Screenshots of a new feature come in two versions: Chinese in `docs/images/`, an identical English one in `docs/images/en/` (same file name). Add new UI strings to the English dictionary too.

## 几条规矩 / House rules

- 只听 `127.0.0.1`；写操作必须带令牌。 / Listen on `127.0.0.1` only; writes need the token.
- 删除一律移到废纸篓，不直接删。 / Every removal goes to the Trash, never a hard delete.
- API key 只放钥匙串。 / API keys live only in the Keychain.
- 新加第三方代码、模型或音效，先确认许可，再写进 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
  Check the license of any new third-party code, model or sound, then add it to [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- 新接外部服务，先按 [docs/services.md](docs/services.md) 的方法核查能不能用。
  Before wiring up a new outside service, verify it the way [docs/services.en.md](docs/services.en.md) describes.

提交代码即表示你同意以 [MIT](LICENSE) 许可发布。 / By contributing you agree to release your work under the [MIT](LICENSE) license.
