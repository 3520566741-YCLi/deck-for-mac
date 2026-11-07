"""GitHub：用本机已登录的 GitHub CLI（gh）读账号概况、所有仓库、浏览量和本机副本状态。不保存任何令牌。

省调用（11-07）：
- 待审 PR、未读通知：每分钟（卡片上要及时）。
- 仓库列表：一次 GraphQL 拿全（星、描述、可见性、CI、PR/Issue 数、最近提交），10 分钟一次。
- 浏览量 / 克隆：GitHub 只留 14 天，每 6 小时查一次，按天存在本机，攒成 30 / 90 天曲线。
- 本机副本：30 分钟找一次仓库在哪个文件夹，每分钟 git status 一下（不联网）。
- 单个仓库的提交、访客来源、PR 列表：点开详情时才查，缓存 10 分钟。
"""
import json
import os
import re
import threading
import time
from datetime import datetime, timezone

from .util import HOME, SourceError, expand, find_exe, run

STORE_DIR = os.path.join(HOME, "Library", "Application Support", "Deck")
TRAFFIC_FILE = os.path.join(STORE_DIR, "github-traffic.json")
REPO_TTL = 600
TRAFFIC_TTL = 6 * 3600
SCAN_TTL = 1800
DETAIL_TTL = 600
KEEP_DAYS = 400
IDLE_DAYS = 60
# 找本机副本的地方（深度 3 以内），config 里 local_roots 可以改
DEFAULT_ROOTS = ["~", "~/Documents/Codex"]
SKIP_DIRS = {"Library", "Applications", "Downloads", "Movies", "Music", "Pictures", "Public", "node_modules", ".Trash", ".cache", ".npm", ".cargo", ".rustup", ".venv", "venv", "build", "dist", "DerivedData"}

# 没写分组的仓库按这张表分；不在表里的进「其他」。config 里 groups 可以覆盖
DEFAULT_GROUPS = [
    # ("分组名", ["仓库名", ...])，例：("Deck", ["deck-for-mac"])
]
# 仓库没写中文简介时用这里的；config 里 descriptions 可以覆盖
DEFAULT_DESC = {
    # "仓库名": "一句中文简介"
}
DEFAULT_ALIAS = {}  # "仓库名": "显示名"

REPO_QUERY = """
query($cursor: String, $since: GitTimestamp!) {
  viewer {
    repositories(first: 50, after: $cursor, ownerAffiliations: OWNER, orderBy: {field: PUSHED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name nameWithOwner description url isPrivate isArchived isFork pushedAt createdAt diskUsage
        stargazerCount forkCount
        primaryLanguage { name }
        repositoryTopics(first: 6) { nodes { topic { name } } }
        pullRequests(states: OPEN) { totalCount }
        issues(states: OPEN) { totalCount }
        latestRelease { tagName publishedAt }
        defaultBranchRef { name target { ... on Commit {
          committedDate messageHeadline
          statusCheckRollup { state }
          history(since: $since) { totalCount }
        } } }
      }
    }
  }
}"""


def _iso(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return None


def _cjk(s):
    return bool(s and re.search(r"[一-鿿]", s))


def _slug_from_remote(url):
    """git@github.com:a/b.git、https://github.com/a/b(.git) → a/b（小写）"""
    m = re.search(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", url or "")
    return ("%s/%s" % (m.group(1), m.group(2))).lower() if m else None


class GitHubSource:
    key = "github"
    title = "GitHub"
    default_interval = 60  # gh API 每小时 5000 次额度，每分钟几次远够（11-06）

    def __init__(self, cfg):
        self.cfg = cfg or {}
        self.gh = None
        self.repos = []
        self.repos_at = 0
        self.traffic_at = 0
        self.scan_at = 0
        self.local = {}  # owner/name 小写 → 本机路径
        self.details = {}  # owner/name → (时间, 详情)
        self.lock = threading.Lock()
        self.traffic = self._load_traffic()

    # ---------- gh ----------
    def _gh(self):
        if not self.gh:
            self.gh = find_exe("gh", [self.cfg.get("gh_path")] if self.cfg.get("gh_path") else [])
        return self.gh

    def _api(self, gh, path, timeout=20):
        code, out, err = run([gh, "api", "-H", "Accept: application/vnd.github+json", path], timeout=timeout)
        if code != 0:
            if "auth login" in err or "401" in err:
                raise SourceError("GitHub CLI 还没登录：在终端运行 gh auth login")
            return None
        try:
            return json.loads(out)
        except ValueError:
            return None

    def _graphql(self, gh, query, **vars_):
        cmd = [gh, "api", "graphql", "-f", "query=" + query]
        for k, v in vars_.items():
            if v is not None:
                cmd += ["-f", "%s=%s" % (k, v)]
        code, out, err = run(cmd, timeout=40)
        if code != 0:
            return None
        try:
            return json.loads(out)
        except ValueError:
            return None

    # ---------- 仓库列表 ----------
    def _fetch_repos(self, gh):
        since = datetime.fromtimestamp(time.time() - 14 * 86400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        nodes, cursor = [], None
        for _ in range(10):  # 最多 500 个仓库
            r = self._graphql(gh, REPO_QUERY, cursor=cursor, since=since)
            page = (((r or {}).get("data") or {}).get("viewer") or {}).get("repositories")
            if not page:
                return None if not nodes else nodes
            nodes += page.get("nodes") or []
            info = page.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                break
            cursor = info.get("endCursor")
        return nodes

    # ---------- 浏览量（本机按天存档） ----------
    def _load_traffic(self):
        try:
            with open(TRAFFIC_FILE, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _fetch_traffic(self, gh, repos):
        cut = time.strftime("%Y-%m-%d", time.gmtime(time.time() - KEEP_DAYS * 86400))
        for r in repos:
            if r.get("isArchived"):
                continue
            slug, got, denied = r["nameWithOwner"], {}, False
            for kind in ("views", "clones"):
                try:
                    d = self._api(gh, "repos/%s/traffic/%s" % (slug, kind))
                except SourceError:
                    return  # gh 登录掉了：这一轮不查了，等下一轮
                if not isinstance(d, dict):
                    denied = True  # 免费账号下私人仓库没有流量统计
                    continue
                got[kind] = {(it.get("timestamp") or "")[:10]: [it.get("count") or 0, it.get("uniques") or 0]
                             for it in d.get(kind) or [] if it.get("timestamp")}
            # 和读的地方共用一把锁：采集线程和浏览量线程不会同时改、读同一个字典
            with self.lock:
                old = self.traffic.get(slug) or {}
                rec = {"views": dict(old.get("views") or {}), "clones": dict(old.get("clones") or {})}
                for kind, days in got.items():
                    rec[kind].update(days)  # GitHub 给的当天数还会涨，同一天以新的为准
                for kind in ("views", "clones"):
                    rec[kind] = {k: v for k, v in rec[kind].items() if k >= cut}
                if denied and not got:
                    rec["denied"] = True
                self.traffic[slug] = rec
        with self.lock:
            snap = json.loads(json.dumps(self.traffic))
        self._save(snap)

    def _save(self, data):
        try:
            os.makedirs(STORE_DIR, exist_ok=True)
            tmp = TRAFFIC_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, TRAFFIC_FILE)
        except OSError:
            pass

    def _traffic_sum(self, slug, kind, days):
        rec = (self.traffic.get(slug) or {}).get(kind) or {}  # 写的一方整份替换，这里读到的是旧的或新的一份，不会读到一半
        cut = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
        return sum(v[0] for k, v in rec.items() if k >= cut)

    def _traffic_series(self, slug, days=30):
        rec = self.traffic.get(slug) or {}  # 同上：整份替换，读时不加锁
        out = []
        for i in range(days - 1, -1, -1):
            day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - i * 86400))
            v = (rec.get("views") or {}).get(day) or [0, 0]
            out.append([day, v[0], v[1]])
        return out

    # ---------- 本机副本 ----------
    def _scan_local(self):
        roots = self.cfg.get("local_roots") or DEFAULT_ROOTS
        found = {}
        for root in roots:
            root = expand(root)
            if not os.path.isdir(root):
                continue
            base = root.rstrip("/").count("/")
            for d, subs, _ in os.walk(root):
                depth = d.rstrip("/").count("/") - base
                if ".git" in subs:
                    code, out, _ = run(["git", "-C", d, "config", "--get", "remote.origin.url"], timeout=5)
                    slug = _slug_from_remote(out.strip()) if code == 0 else None
                    if slug and slug not in found:
                        found[slug] = d
                    subs[:] = []
                    continue
                subs[:] = [s for s in subs if not s.startswith(".") and s not in SKIP_DIRS] if depth < 3 else []
        self.local = found

    def _local_state(self, path):
        code, out, _ = run(["git", "-C", path, "status", "--porcelain=v1", "-b"], timeout=5)
        if code != 0:
            return None
        lines = out.splitlines()
        head = lines[0] if lines and lines[0].startswith("##") else ""
        changed = len([l for l in lines if not l.startswith("##")])
        ahead = int((re.search(r"ahead (\d+)", head) or [0, 0])[1])
        branch = (re.match(r"## (?:No commits yet on |Initial commit on )?([^.\s]+)", head) or [None, None])[1]
        return {"path": path.replace(HOME, "~", 1), "changed": changed, "ahead": ahead, "branch": branch}

    # ---------- 整理成页面要的样子 ----------
    def _shape(self, r, local):
        name, slug = r["name"], r["nameWithOwner"]
        groups = self.cfg.get("groups") or {}
        group = next((g for g, names in (list(groups.items()) or DEFAULT_GROUPS) if name in names), None)
        if not group and name.lower().startswith(("Deck", "deck")):
            group = "Mac 管家全家"  # 管家的私人 / 开源仓库都归这一组
        # 中文简介：config 里写的 > GitHub 上的中文描述 > 内置对照表 > GitHub 上的英文描述
        gd = r.get("description") or ""
        desc = (self.cfg.get("descriptions") or {}).get(name) or (gd if _cjk(gd) else "") or DEFAULT_DESC.get(name) or gd
        head = ((r.get("defaultBranchRef") or {}).get("target")) or {}
        ci = ((head.get("statusCheckRollup") or {}).get("state") or "").upper()
        pushed = _iso(r.get("pushedAt") or "")
        idle_days = int((time.time() - pushed) / 86400) if pushed else None
        lc = self._local_state(local) if local else None
        tr = self.traffic.get(slug) or {}
        # 状态：一个仓库只报一件最要紧的事
        if r.get("isArchived"):
            st = ["idle", "已归档"]
        elif ci in ("FAILURE", "ERROR"):
            st = ["err", "CI 失败"]
        elif lc and (lc["changed"] or lc["ahead"]):
            parts = (["%d 个改动没提交" % lc["changed"]] if lc["changed"] else []) + (["%d 个提交没推" % lc["ahead"]] if lc["ahead"] else [])
            st = ["warn", "本机 " + "，".join(parts)]
        elif idle_days is not None and idle_days >= IDLE_DAYS:
            st = ["idle", "%d 天没动" % idle_days]
        elif ci == "PENDING":
            st = ["ok", "CI 跑着"]
        elif ci == "SUCCESS":
            st = ["ok", "CI 通过"]
        else:
            st = ["ok", "正常"]
        if r.get("isArchived"):
            group = "已归档"
        return {
            "name": name, "slug": slug, "alias": (self.cfg.get("aliases") or {}).get(name) or DEFAULT_ALIAS.get(name),
            "group": group or "其他", "desc": desc, "private": r.get("isPrivate"), "archived": r.get("isArchived"),
            "fork": r.get("isFork"), "url": r.get("url"), "pushed_at": pushed, "created_at": _iso(r.get("createdAt") or ""),
            "stars": r.get("stargazerCount") or 0, "forks": r.get("forkCount") or 0, "size_kb": r.get("diskUsage") or 0,
            "language": (r.get("primaryLanguage") or {}).get("name"),
            "topics": [n["topic"]["name"] for n in ((r.get("repositoryTopics") or {}).get("nodes") or []) if n.get("topic")],
            "prs": ((r.get("pullRequests") or {}).get("totalCount")) or 0, "issues": ((r.get("issues") or {}).get("totalCount")) or 0,
            "branch": (r.get("defaultBranchRef") or {}).get("name"), "ci": ci or None,
            "last_commit": {"title": head.get("messageHeadline"), "at": _iso(head.get("committedDate") or "")} if head else None,
            "commits_14d": ((head.get("history") or {}).get("totalCount")) or 0,
            "release": {"tag": r["latestRelease"].get("tagName"), "at": _iso(r["latestRelease"].get("publishedAt") or "")} if r.get("latestRelease") else None,
            "views_30d": self._traffic_sum(slug, "views", 30), "views_7d": self._traffic_sum(slug, "views", 7),
            "clones_30d": self._traffic_sum(slug, "clones", 30), "traffic": not tr.get("denied") and bool(tr),
            "local": lc, "status": st, "need": st[0] in ("err", "warn"), "idle_days": idle_days,
            "suggest": "archive" if (not r.get("isArchived") and idle_days is not None and idle_days >= 90 and r.get("isPrivate")) else None,
        }

    def collect(self, force=False):
        gh = self._gh()
        if not gh:
            raise SourceError("没找到 GitHub CLI（gh）。用 brew install gh 安装，再运行 gh auth login。")
        user = self._api(gh, "user")
        if not isinstance(user, dict) or not user.get("login"):
            raise SourceError("连不上 GitHub（检查网络，或运行 gh auth status 看登录状态）")
        login = user["login"]
        now = time.time()
        if force or now - self.repos_at > REPO_TTL or not self.repos:
            got = self._fetch_repos(gh)
            if got is not None:
                self.repos, self.repos_at = got, now
        if now - self.scan_at > SCAN_TTL:
            self._scan_local()
            self.scan_at = now
        if self.repos and now - self.traffic_at > TRAFFIC_TTL:
            self.traffic_at = now
            threading.Thread(target=self._fetch_traffic, args=(gh, list(self.repos)), daemon=True).start()

        def search(q):
            r = self._api(gh, "search/issues?per_page=5&q=" + q.replace(" ", "+"))
            if not isinstance(r, dict):
                return {"count": None, "items": []}
            return {"count": r.get("total_count"), "items": [
                {"title": i.get("title"), "repo": (i.get("repository_url") or "").split("/repos/")[-1],
                 "number": i.get("number"), "url": i.get("html_url"), "updated_at": _iso(i.get("updated_at") or "")}
                for i in r.get("items", [])]}

        review = search("is:open is:pr archived:false review-requested:@me")
        mine = search("is:open is:pr archived:false author:@me")
        notes = self._api(gh, "notifications?per_page=50")
        unread = len(notes) if isinstance(notes, list) else None
        repos = [self._shape(r, self.local.get(r["nameWithOwner"].lower())) for r in self.repos]
        live = [r for r in repos if not r["archived"]]
        recent = [{"name": r["name"], "private": r["private"], "language": r["language"], "stars": r["stars"],
                   "pushed_at": r["pushed_at"], "url": r["url"]} for r in repos[:5]]
        pushed_today = sum(1 for r in repos if (r["pushed_at"] or 0) > now - 86400)
        needs = [r for r in live if r["need"]]
        n_need = (review["count"] or 0) + len(needs)
        level = "warn" if n_need else "ok"
        why = ([{"text": "有 %d 个 PR 等你审" % review["count"], "fix": "点卡片看是哪几个"}] if review["count"] else []) + \
            [{"text": "%s：%s" % (r["alias"] or r["name"], r["status"][1]), "fix": "点卡片看详情"} for r in needs[:3]]
        return {"level": level, "why": why, "login": login, "name": user.get("name"), "avatar": user.get("avatar_url"),
                "followers": user.get("followers"),
                "repos": {"total": len(live), "private": sum(1 for r in live if r["private"]),
                          "public": sum(1 for r in live if not r["private"]), "archived": len(repos) - len(live),
                          "pushed_today": pushed_today},
                "stars": sum(r["stars"] for r in live), "views_30d": sum(r["views_30d"] for r in live),
                "views_7d": sum(r["views_7d"] for r in live), "need": n_need,
                "list": repos, "list_at": self.repos_at, "traffic_at": self.traffic_at or None,
                "review_requests": review, "my_prs": mine, "unread": unread, "recent": recent}

    # ---------- 单个仓库详情（点开时才查） ----------
    def detail(self, slug):
        slug = str(slug or "")
        hit = next((r for r in self.repos if r["nameWithOwner"] == slug), None)
        if not hit:
            return {"error": "没找到这个仓库"}
        with self.lock:
            c = self.details.get(slug)
            if c and time.time() - c[0] < DETAIL_TTL:
                return c[1]
        gh = self._gh()
        if not gh:
            return {"error": "没找到 GitHub CLI（gh）"}
        try:
            commits = self._api(gh, "repos/%s/commits?per_page=8" % slug) or []
            pulls = self._api(gh, "repos/%s/pulls?state=open&per_page=5" % slug) or []
            refs = self._api(gh, "repos/%s/traffic/popular/referrers" % slug)
            runs = self._api(gh, "repos/%s/actions/runs?per_page=3" % slug)
        except SourceError as e:
            return {"error": str(e)}
        out = {
            "commits": [{"sha": (c.get("sha") or "")[:7], "title": ((c.get("commit") or {}).get("message") or "").split("\n")[0],
                         "at": _iso(((c.get("commit") or {}).get("committer") or {}).get("date") or ""), "url": c.get("html_url")}
                        for c in commits if isinstance(c, dict)],
            "pulls": [{"number": p.get("number"), "title": p.get("title"), "at": _iso(p.get("updated_at") or ""),
                       "url": p.get("html_url"), "draft": p.get("draft")} for p in pulls if isinstance(p, dict)],
            "referrers": [{"name": x.get("referrer"), "count": x.get("count"), "uniques": x.get("uniques")}
                          for x in (refs if isinstance(refs, list) else [])][:5],
            "runs": [{"name": x.get("name"), "status": x.get("status"), "conclusion": x.get("conclusion"),
                      "at": _iso(x.get("updated_at") or ""), "url": x.get("html_url")}
                     for x in ((runs or {}).get("workflow_runs") or [])][:3] if isinstance(runs, dict) else [],
            "views": self._traffic_series(slug, 30),
            "traffic": isinstance(refs, list),
            "at": time.time(),
        }
        with self.lock:
            self.details[slug] = (time.time(), out)
        return out
