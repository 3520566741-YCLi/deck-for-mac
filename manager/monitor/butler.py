"""Mac 管家：把管家已登记的自制应用健康状况汇总成一张卡（不测显存、不调 DeepSeek，零成本）。"""


class ButlerSource:
    key = "butler"
    title = "我的应用"  # 11-06 主页重排：和顶上的「管家」页签区分开
    default_interval = 30  # 11-06：尽量实时

    def __init__(self, cfg, collect_fn=None):
        self.collect_fn = collect_fn

    def collect(self, force=False):
        snap = self.collect_fn(deep=False)
        apps = snap.get("apps", [])
        bad = [a for a in apps if a.get("level") != "ok"]
        level = "error" if any(a["level"] == "error" for a in bad) else "warn" if bad or snap.get("dock_broken") else "ok"
        why = [{"text": "%s：%s" % (a.get("name"), (a.get("issues") or ["状态不对"])[0]), "fix": "点卡片看详情"} for a in bad[:6]]
        if snap.get("dock_broken"):
            why.append({"text": "程序坞里有 %d 个失效的图标" % len(snap["dock_broken"]), "fix": "从程序坞拖掉它们"})
        return {"level": level, "why": why, "total": len(apps), "ok": len(apps) - len(bad),
                "running": sum(1 for a in apps if a.get("running")),
                "issues": [{"name": a.get("name"), "level": a.get("level"), "text": (a.get("issues") or [""])[0]}
                           for a in sorted(bad, key=lambda a: a["level"] != "error")][:6],
                "dock_broken": len(snap.get("dock_broken") or []),
                "apps": [{"name": a.get("name"), "running": a.get("running"), "level": a.get("level"),
                          "cpu": a.get("cpu"), "mem_mb": a.get("rss_mb"), "issue": (a.get("issues") or [None])[0]} for a in apps]}
