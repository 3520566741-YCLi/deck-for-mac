#!/usr/bin/env python3
"""Claude Code 的状态栏命令（~/.claude/settings.json 的 statusLine）。

Claude Code 每次刷新状态栏时把一段 JSON 从 stdin 传进来，订阅账号里带 rate_limits：
  {"rate_limits": {"five_hour": {"used_percentage": 23.5, "resets_at": 1738425600}, "seven_day": {...}}}
这里只把 rate_limits 记到 ~/deck-for-mac/logs/claude-rate-limits.json，给 Deck 的 Claude 卡用，
这样不用再去查会限流的用量接口。不碰登录信息。输出一行简短的状态栏文字。
"""
import json
import os
import sys
import time

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "claude-rate-limits.json")


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return
    rl = data.get("rate_limits") if isinstance(data, dict) else None
    parts = []
    if isinstance(rl, dict) and rl:
        keep = {}
        for k in ("five_hour", "seven_day"):
            w = rl.get(k)
            if isinstance(w, dict) and isinstance(w.get("used_percentage"), (int, float)):
                keep[k] = {"used_percentage": float(w["used_percentage"]), "resets_at": w.get("resets_at")}
        if keep:
            try:
                os.makedirs(os.path.dirname(OUT), exist_ok=True)
                tmp = OUT + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"at": time.time(), "rate_limits": keep}, f)
                os.replace(tmp, OUT)
            except OSError:
                pass
            for k, label in (("five_hour", "5h"), ("seven_day", "周")):
                if k in keep:
                    parts.append("%s 剩 %d%%" % (label, round(100 - keep[k]["used_percentage"])))
    model = ((data.get("model") or {}).get("display_name") if isinstance(data, dict) else None) or ""
    print(" · ".join([p for p in [model] + parts if p]))


if __name__ == "__main__":
    main()
