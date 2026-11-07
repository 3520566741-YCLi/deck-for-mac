#!/bin/bash
# 启动 Mac 管家（已在运行就直接打开页面）
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=$(python3 -c "import json,os;p='$DIR/config.json';print(json.load(open(p)).get('port',8777) if os.path.exists(p) else 8777)")
if curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
  open "http://127.0.0.1:$PORT/"
else
  nohup /usr/bin/python3 "$DIR/server.py" >"$DIR/server.log" 2>&1 &
fi
