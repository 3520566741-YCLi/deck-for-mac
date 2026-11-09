"""Agent 进度卡（11-09）：Claude Code / Codex / DeepSeek Harness 自己报的「完成了百分之几」。
数据是 agentpace 小工具写在本机的小 JSON，读一次只是列一个小目录，所以 3 秒一刷；没人看监控台时 Hub 自己停。"""
import agentpace


class AgentsSource:
    key = "agents"
    title = "Agent 进度"
    default_interval = 3

    def __init__(self, cfg):
        self.cfg = cfg or {}

    def collect(self, force=False):
        if force:
            agentpace.status(fresh=True)
        d = agentpace.snapshot()
        agentpace.auto_kick(d["tasks"])  # 有新进展就让管家（DeepSeek）在后台整理一下，不等它
        return d
