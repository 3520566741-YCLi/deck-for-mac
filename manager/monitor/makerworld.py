"""MakerWorld（拓竹模型社区）账号概况：公开接口，不需要登录。配置里填 user_id 才启用。"""
from .util import SourceError, http_json

API = "https://api.bambulab.com/v1/design-user-service/user/profile/%s"


class MakerWorldSource:
    key = "makerworld"
    title = "MakerWorld"
    default_interval = 1800

    def __init__(self, cfg):
        self.cfg = cfg or {}

    def collect(self, force=False):
        uid = str(self.cfg.get("user_id") or "").strip()
        if not uid.isdigit():
            raise SourceError("在 config.json 的 dashboard.sources.makerworld.user_id 填你的 MakerWorld 用户 ID")
        status, p = http_json(API % uid, {"User-Agent": "Mozilla/5.0 (Macintosh)"}, timeout=12)
        if status != 200 or not isinstance(p, dict):
            raise SourceError("MakerWorld 接口暂时不可用（%s）" % (status or "网络错误"))
        mw = p.get("MWCount") or {}
        designs = ((p.get("personal") or {}).get("designsInfo") or [])[:4]
        return {"level": "ok", "name": p.get("name"), "handle": p.get("handle"),
                "fans": p.get("fanCount"), "likes": p.get("likeCount"), "collections": p.get("collectionCount"),
                "designs": mw.get("designCount"), "downloads": mw.get("myDesignDownloadCount"),
                "prints": mw.get("myDesignPrintCount"),
                "recent": [{"title": d.get("title"), "id": d.get("id")} for d in designs]}
