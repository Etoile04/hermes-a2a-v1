#!/usr/bin/env python3
"""ThinkStation A2A Proactive Notifier

由 ThinkStation 上的 cron 或 systemd timer 触发，
主动向 Mac Studio Gateway 发送 A2A 消息。

用法:
  python3 a2a_notify.py "消息内容"
  echo "消息内容" | python3 a2a_notify.py -
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

# 目标 Gateway 配置
MAC_GATEWAY = os.environ.get("A2A_TARGET_URL", "http://100.65.135.2:18802")
TIMEOUT = int(os.environ.get("A2A_TIMEOUT", "300"))

# 通知记录（简单的追加日志）
NOTIFY_LOG = os.path.expanduser("~/projects/hermes-a2a-v1/notify.log")


def send_a2a_message(text: str) -> dict:
    """发送 A2A v1.0 消息并返回完整响应。"""
    payload = {
        "jsonrpc": "2.0",
        "method": "SendMessage",
        "id": int(time.time()),
        "params": {
            "message": {
                "role": "ROLE_USER",
                "parts": [{"text": text}],
                "messageId": f"notify-{int(time.time())}",
            }
        }
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{MAC_GATEWAY}/",
        data=data,
        headers={
            "Content-Type": "application/json",
            "A2A-Version": "1.0",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def log_notification(direction: str, content: str, task_id: str = ""):
    """追加通知记录。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(NOTIFY_LOG, "a") as f:
        f.write(f"[{ts}] {direction} task={task_id} content={content[:100]}\n")


def main():
    # 读取消息内容
    if len(sys.argv) > 1:
        if sys.argv[1] == "-":
            text = sys.stdin.read().strip()
        else:
            text = " ".join(sys.argv[1:])
    else:
        text = f"ThinkStation 定期心跳检测 — {time.strftime('%Y-%m-%d %H:%M:%S')}"

    if not text:
        print("[NOTIFY] 错误: 消息内容为空")
        sys.exit(1)

    print(f"[NOTIFY] → 发送至 {MAC_GATEWAY}: {text[:60]}...")
    sys.stdout.flush()

    try:
        result = send_a2a_message(text)
    except Exception as e:
        print(f"[NOTIFY] ✗ 发送失败: {e}")
        log_notification("FAIL", str(e))
        sys.exit(1)

    # 解析
    task = result.get("result", {}).get("task", {})
    task_id = task.get("id", "N/A")
    status = task.get("status", {})
    state = status.get("state", "")
    parts = status.get("message", {}).get("parts", [])
    reply = "".join(p.get("text", "") for p in parts)

    print(f"[NOTIFY] ✓ 状态: {state}")
    print(f"[NOTIFY] ✓ 回复: {reply[:200]}")
    print(f"[NOTIFY] ✓ task_id: {task_id}")

    log_notification("SENT", text, task_id)
    log_notification("RECV", reply, task_id)


if __name__ == "__main__":
    main()
