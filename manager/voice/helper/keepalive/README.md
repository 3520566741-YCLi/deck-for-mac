# DeckVoice 看门狗

登录时和之后每 5 分钟检查一次：DeckVoice 没在运行，就在后台打开它（它会把语音服务 8778 一起拉起来）。
DeckVoice 崩溃由语音服务自己拉起（hub.py），两个都没了（正常退出、被系统杀掉、重启后没人打开）就靠这里。

## 安装 / 更新
```bash
D="$HOME/Library/Application Support/Deck/voice"
cp manager/voice/helper/keepalive/keepalive.sh "$D/" && chmod +x "$D/keepalive.sh"
sed "s#__HOME__#$HOME#g" manager/voice/helper/keepalive/io.github.deckformac.deckvoice.keepalive.plist > ~/Library/LaunchAgents/io.github.deckformac.deckvoice.keepalive.plist
launchctl bootout gui/$(id -u)/io.github.deckformac.deckvoice.keepalive 2>/dev/null; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.github.deckformac.deckvoice.keepalive.plist
```

## 暂停 / 卸载
- 暂停：`touch ~/Library/Application\ Support/Deck/voice/keepalive-off`
- 卸载：`launchctl bootout gui/$(id -u)/io.github.deckformac.deckvoice.keepalive`，再把 `~/Library/LaunchAgents/io.github.deckformac.deckvoice.keepalive.plist` 移到废纸篓。
- 日志：`~/Library/Logs/DeckVoice-keepalive.log`
