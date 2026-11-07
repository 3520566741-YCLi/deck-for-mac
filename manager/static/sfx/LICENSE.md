# Deck 按键音效的音源

| 文件 | 来源 | 处理 |
|---|---|---|
| tap.wav | snd-lib 1.2.4 · SND01「sine」tap_01（土屋泰洋） | 4.2 kHz 低通、2.2 kHz 以上压 3 dB |
| primary.wav | Kenney Interface Sounds · bong_001 | 3.2 kHz 低通 |
| tab.wav | SND01 toggle_on | 降约 4 个半音，切掉 500 Hz 以下共鸣和 3.4 kHz 以上亮声，剪到 60 ms |
| on.wav / off.wav | SND01 toggle_on / toggle_off | 降约 6 个半音，3.2 kHz 低通 |
| open.wav / close.wav | SND01 select | 升 / 降调，5 kHz 低通 |
| page.wav | SND01 select | 降调 0.9，3.2 kHz 低通 |
| done.wav | Kenney · confirmation_001 | 4.2 kHz 低通 |
| notify.wav | SND01 notification | 4.2 kHz 低通 |
| warn.wav | Kenney · error_005 | 2.4 kHz 低通 |

- snd-lib：https://github.com/snd-lib/snd-lib ，代码 MIT；音频可免费使用，著作权归各自声音设计师（见 https://snd.dev/）。
- Kenney Interface Sounds：https://kenney.nl/assets/interface-sounds ，CC0。
- 所有文件都去掉了开头空白（按下就响）并按「越常用越轻」对齐了响度。处理脚本和试听页在项目文件 deck-sound/。
