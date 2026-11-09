# Agent 进度稳定性测试（11-09）：python3 manager/agentpace/stress_test.py（在临时 HOME 里跑，不碰真实配置）
# 并发 20 个会话、坏数据、目录写不进、强杀、乱输入、5000 次长跑、开关立即失效、孤儿进程。
import json, os, subprocess, sys, tempfile, threading, time, resource, signal
H = tempfile.mkdtemp(); os.environ["HOME"] = H
sys.path.insert(0, "/home/user/deck-for-mac/manager")
import agentpace as A
A._install_script()
for a in A.AGENTS: A._set_flag(a, True)
def spawn(agent):
    cmd = A.command(agent); cmd[0] = sys.executable
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
def rpc(p, i, m, params=None):
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": m, "params": params or {}}) + "\n"); p.stdin.flush()
    return json.loads(p.stdout.readline())
errs = []
# 1. 并发：20 个会话各报 200 次，同时另一线程不停读
stop = False
def reader():
    n = 0
    while not stop:
        try: A.tasks(); n += 1
        except Exception as e: errs.append(("reader", repr(e)))
    print("reader loops", n)
def session(k):
    p = spawn(["claude", "codex", "deepseek"][k % 3])
    rpc(p, 0, "initialize")
    for i in range(200):
        r = rpc(p, i + 1, "tools/call", {"name": "report_progress", "arguments": {"percent": i % 101, "task": "任务%d" % k, "step": "第%d步" % i}})
        if r["result"]["isError"]: errs.append(("call", r))
    p.stdin.close(); p.wait(5)
t = threading.Thread(target=reader); t.start()
t0 = time.time()
ths = [threading.Thread(target=session, args=(k,)) for k in range(20)]
[x.start() for x in ths]; [x.join() for x in ths]
stop = True; t.join()
print("1 并发 4000 次上报用时 %.1fs，错误 %d，结束后任务 %d 个，状态 %s" % (time.time() - t0, len(errs), len(A.tasks()), {x["state"] for x in A.tasks()}))
# 2. 坏数据
R = A.REPORTS
open(R + "/claude-1.json", "w").write("{not json")
open(R + "/codex-2.json", "w").write(json.dumps({"agent": "codex", "percent": "50"}))
open(R + "/claude-3.json", "w").write(json.dumps({"agent": "claude", "percent": 50, "pid": "abc", "log": "x", "task": ["a"], "at": "now"}))
open(R + "/claude-4.json", "w").write("x" * 20000)
open(R + "/evil-5.json", "w").write(json.dumps({"agent": "rm -rf", "percent": 1}))
open(R + "/claude-6.json", "w").write(json.dumps({"agent": "claude", "percent": 999, "pid": os.getpid(), "at": time.time(), "log": [{"t": 1, "p": 5000}, 3, {"t": "x"}], "task": "<img src=x onerror=alert(1)>"}))
try:
    ts = A.tasks(); print("2 坏数据：没崩，读到", [(x["id"], x["percent"], x["task"]) for x in ts if x["id"] in ("claude-3", "claude-6")], "超大文件已删:", not os.path.exists(R + "/claude-4.json"))
except Exception as e: print("2 坏数据崩了", repr(e))
# 3. 写不进（目录只读）
p = spawn("claude"); rpc(p, 0, "initialize")
os.chmod(R, 0o500)
r = rpc(p, 1, "tools/call", {"name": "report_progress", "arguments": {"percent": 10}})
print("3 目录写不进：工具照样回", r["result"]); os.chmod(R, 0o700)
# 4. 小工具被强杀 / Agent 退出
os.kill(p.pid, signal.SIGKILL); p.wait()
p2 = spawn("codex"); rpc(p2, 0, "initialize"); rpc(p2, 1, "tools/call", {"name": "report_progress", "arguments": {"percent": 40, "task": "被杀测试"}})
os.kill(p2.pid, signal.SIGKILL); p2.wait()
st = [x["state"] for x in A.tasks() if x["task"] == "被杀测试"]
print("4 强杀后状态:", st)
p3 = spawn("deepseek"); rpc(p3, 0, "initialize"); rpc(p3, 1, "tools/call", {"name": "report_progress", "arguments": {"percent": 40, "task": "正常退出"}})
p3.stdin.close(); p3.wait(5)
print("4 Agent 关掉会话后:", [x["state"] for x in A.tasks() if x["task"] == "正常退出"], "退出码", p3.returncode)
# 坏输入不崩
p4 = spawn("claude")
p4.stdin.write("garbage\n" + "{" * 10 + "\n" + json.dumps([1, 2]) + "\n" + "x" * 300000 + "\n"); p4.stdin.flush()
print("4 乱输入后还活着:", rpc(p4, 9, "ping")["result"] == {})
# 5. 长时间：一个会话 5000 次，看内存和单次耗时
t0 = time.time()
for i in range(5000):
    rpc(p4, 100 + i, "tools/call", {"name": "report_progress", "arguments": {"percent": i % 100, "task": "长跑", "step": "s%d" % (i % 37)}})
dt = (time.time() - t0) / 5000 * 1000
rss = subprocess.run(["ps", "-o", "rss=", "-p", str(p4.pid)], capture_output=True, text=True).stdout.strip()
print("5 单次上报 %.2f ms，小工具进程内存 %s KB，报告文件 %d 字节" % (dt, rss, os.path.getsize(R + "/claude-%d.json" % p4.pid)))
p4.stdin.close(); p4.wait(5)
# 6. Deck 读一次的开销
t0 = time.time()
for _ in range(500): A.snapshot()
print("6 Deck 读一次进度 %.2f ms（%d 个文件）" % ((time.time() - t0) / 500 * 1000, len(os.listdir(R))))
# 7. 关掉开关立即失效
p5 = spawn("claude"); rpc(p5, 0, "initialize")
A._set_flag("claude", False)
print("7 关掉开关后:", rpc(p5, 1, "tools/call", {"name": "report_progress", "arguments": {"percent": 1}})["result"]["isError"], len(rpc(p5, 2, "tools/list")["result"]["tools"]))
p5.stdin.close(); p5.wait(5)
print("孤儿进程:", subprocess.run("ps aux | grep deck_pace_mc[p] | wc -l", shell=True, capture_output=True, text=True).stdout.strip())
print("errors:", errs[:3])
