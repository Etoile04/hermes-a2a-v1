#!/usr/bin/env python3
"""A2A Client — 让 ThinkStation 主动向远程 Gateway 发起 A2A 通讯。

用法:
  python3 a2a_ping.py [消息内容] [超时秒数]
"""

import sys
import json
import urllib.request
import urllib.error

# 目标 Gateway（Mac Studio）
TARGET_URL = "http://100.65.135.2:18802"


def send_a2a_message(text: str, timeout: int = 300) -> dict:
    """发送 A2A v1.0 JSON-RPC 消息并返回响应。"""
    payload = {
        "jsonrpc": "2.0",
        "method": "SendMessage",
        "id": 1,
        "params": {
            "message": {
                "role": "ROLE_USER",
                "parts": [{"text": text}],
                "messageId": "msg-ping-from-thinkstation",
            }
        }
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{TARGET_URL}/",
        data=data,
        headers={
            "Content-Type": "application/json",
            "A2A-Version": "1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"error": {"code": e.code, "message": body}}
    except Exception as e:
        return {"error": {"code": -1, "message": str(e)}}


def main():
    text = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "这是一条来自 ThinkStation 的主动 A2A 通讯测试。请确认收到。"
    timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 300

    print(f"[ThinkStation] → 向 {TARGET_URL} 发送 A2A 消息 (timeout={timeout}s)...")
    print(f"[ThinkStation] → 内容: {text[:80]}...")
    sys.stdout.flush()

    result = send_a2a_message(text, timeout)

    if "error" in result:
        print(f"[ThinkStation] ✗ 失败: {result['error']}")
        sys.exit(1)

    task = result.get("result", {}).get("task", {})
    status = task.get("status", {})
    state = status.get("state", "")
    parts = status.get("message", {}).get("parts", [])
    response_text = "".join(p.get("text", "") for p in parts)

    print(f"\n[ThinkStation] ✓ 响应状态: {state}")
    print(f"[Mac Studio] → 回复:")
    print(response_text)
    print(f"\ntask_id: {task.get('id', 'N/A')}")
    print(f"context_id: {task.get('contextId', 'N/A')}")

    return response_text


if __name__ == "__main__":
    main()
