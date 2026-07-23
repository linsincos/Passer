from __future__ import annotations

"""Small stdio MCP server used by OpenClaw to control a running Passer.

The MCP process never imports Passer's UI or executes arbitrary commands.  It
forwards one authenticated, size-limited request to Passer's existing loopback
single-instance socket, where a strict action allowlist is enforced again.
"""

import argparse
import json
import socket
import sys
from pathlib import Path
from typing import Any, Callable


BRIDGE_PROTOCOL = "passer-openclaw-v1"
BRIDGE_SERVER_NAME = "passer-openclaw-bridge"
BRIDGE_SERVER_VERSION = "1.0.0"
DEFAULT_PASSER_PORT = 50719
MAX_MESSAGE_BYTES = 64 * 1024
PASSER_CONTROL_ACTIONS = (
    "status",
    "summon",
    "list_tools",
    "list_items",
    "search",
    "select_item",
    "locate_item",
    "open_item",
    "open_tool",
    "add_target",
    "start_screenshot",
    "clear_search",
)


def _read_token(token_file: str | Path) -> str:
    try:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("Passer OpenClaw 控制令牌不存在，请在 Passer 设置中重新启用 OpenClaw。") from exc
    if len(token) < 32 or len(token) > 256:
        raise RuntimeError("Passer OpenClaw 控制令牌无效，请在 Passer 设置中重新启用 OpenClaw。")
    return token


def _receive_line(conn: socket.socket) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while size <= MAX_MESSAGE_BYTES:
        chunk = conn.recv(min(8192, MAX_MESSAGE_BYTES + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if b"\n" in chunk:
            break
    data = b"".join(chunks)
    if len(data) > MAX_MESSAGE_BYTES:
        raise RuntimeError("Passer OpenClaw 桥返回内容过大。")
    return data.split(b"\n", 1)[0]


def send_passer_request(
    action: str,
    params: dict[str, Any] | None,
    *,
    token_file: str | Path,
    port: int = DEFAULT_PASSER_PORT,
    timeout: float = 35.0,
) -> dict[str, Any]:
    """Send one authenticated control request to the running Passer process."""
    action = str(action or "").strip().casefold()
    if action not in PASSER_CONTROL_ACTIONS:
        raise ValueError(f"OpenClaw 不允许调用 Passer 动作：{action or '（空）'}")
    payload = {
        "protocol": BRIDGE_PROTOCOL,
        "token": _read_token(token_file),
        "action": action,
        "params": dict(params or {}),
    }
    encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ValueError("OpenClaw 发给 Passer 的参数过大。")
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=3.0) as conn:
            conn.settimeout(max(3.0, float(timeout)))
            conn.sendall(encoded)
            raw = _receive_line(conn)
    except OSError as exc:
        raise RuntimeError("无法连接 Passer；请确认 Passer 正在运行且已启用 OpenClaw。") from exc
    try:
        response = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError) as exc:
        raise RuntimeError("Passer OpenClaw 桥返回了无效响应。") from exc
    if not isinstance(response, dict):
        raise RuntimeError("Passer OpenClaw 桥返回了无效响应。")
    if not response.get("ok", False):
        raise RuntimeError(str(response.get("error") or "Passer 拒绝了 OpenClaw 请求。"))
    return response


def _tool_definition() -> dict[str, Any]:
    return {
        "name": "passer_control",
        "description": (
            "Control the user's running Passer desktop app through a local authenticated bridge. "
            "Only non-destructive allowlisted actions are available; deletion, shell execution, "
            "file reading, settings changes, mail sending, browser control and MOD operations are unavailable."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(PASSER_CONTROL_ACTIONS),
                    "description": "Passer action to perform.",
                },
                "query": {"type": "string", "description": "Existing Passer item name or search text."},
                "tool": {"type": "string", "description": "Built-in Passer tool name for open_tool."},
                "target": {
                    "type": "string",
                    "description": "User-supplied local path or URL for add_target.",
                },
            },
            "required": ["action"],
        },
    }


class PasserMCPServer:
    """Protocol handler separated from stdio so it can be unit-tested."""

    def __init__(self, token_file: str | Path, port: int = DEFAULT_PASSER_PORT,
                 sender: Callable[..., dict[str, Any]] = send_passer_request) -> None:
        self.token_file = Path(token_file)
        self.port = int(port)
        self.sender = sender

    @staticmethod
    def _response(request_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": int(code), "message": str(message)},
        }

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return self._error(message.get("id") if isinstance(message, dict) else None,
                               -32600, "Invalid Request")
        request_id = message.get("id")
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if request_id is None:
            return None
        if method == "initialize":
            requested = str(params.get("protocolVersion") or "2025-06-18")
            return self._response(request_id, {
                "protocolVersion": requested,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": BRIDGE_SERVER_NAME, "version": BRIDGE_SERVER_VERSION},
                "instructions": "Use passer_control only when the user explicitly asks to operate Passer.",
            })
        if method == "ping":
            return self._response(request_id, {})
        if method == "tools/list":
            return self._response(request_id, {"tools": [_tool_definition()]})
        if method == "tools/call":
            if str(params.get("name") or "") != "passer_control":
                return self._error(request_id, -32602, "Unknown tool")
            arguments = params.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            action = str(arguments.get("action") or "").strip().casefold()
            forwarded = {
                key: str(arguments.get(key) or "").strip()
                for key in ("query", "tool", "target")
                if str(arguments.get(key) or "").strip()
            }
            try:
                response = self.sender(
                    action,
                    forwarded,
                    token_file=self.token_file,
                    port=self.port,
                )
                text = json.dumps(response.get("result"), ensure_ascii=False, indent=2)
                return self._response(request_id, {
                    "content": [{"type": "text", "text": text}],
                    "structuredContent": response.get("result"),
                    "isError": False,
                })
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                return self._response(request_id, {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                })
        return self._error(request_id, -32601, f"Method not found: {method}")


def run_mcp_stdio(token_file: str | Path, port: int = DEFAULT_PASSER_PORT) -> int:
    server = PasserMCPServer(token_file, port)
    for raw_line in sys.stdin.buffer:
        if not raw_line.strip():
            continue
        try:
            message = json.loads(raw_line.decode("utf-8"))
            response = server.handle(message)
        except (UnicodeError, ValueError, TypeError) as exc:
            response = PasserMCPServer._error(None, -32700, f"Parse error: {exc}")
        if response is None:
            continue
        encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        sys.stdout.write(encoded + "\n")
        sys.stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Passer OpenClaw MCP bridge")
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_PASSER_PORT)
    args = parser.parse_args(argv)
    return run_mcp_stdio(args.token_file, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
