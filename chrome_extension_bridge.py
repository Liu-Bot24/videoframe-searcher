from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import threading
import uuid
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
LOG_DIR = ROOT_DIR / "logs"
LOG_FILE = LOG_DIR / "chrome_extension_bridge.log"
DEFAULT_IMAGE_PATH = Path(
    r"D:\work\Claude Code\VideoFrame Searcher\workspace\20260320_123347_#渣男探花_#探花系列_三好学生，团支书\screenshots\frame_00010.jpg"
)


def _bj_now() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S +08:00")


def _log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{_bj_now()} {message}"
    print(message)
    with LOG_FILE.open("a", encoding="utf-8") as file:
        file.write(line + "\n")


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Private-Network", "true")
    handler.end_headers()
    handler.wfile.write(body)


def _read_image_as_payload(image_path: Path) -> dict[str, str | int]:
    if not image_path.exists():
        raise FileNotFoundError(f"图片不存在：{image_path}")
    if not image_path.is_file():
        raise RuntimeError(f"不是文件：{image_path}")

    raw = image_path.read_bytes()
    mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(raw).decode("ascii")
    return {
        "image_path": str(image_path),
        "file_name": image_path.name,
        "mime_type": mime,
        "base64_data": encoded,
        "size_bytes": len(raw),
    }


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict:
    raw_len = handler.headers.get("Content-Length", "0")
    try:
        length = int(raw_len)
    except ValueError:
        return {}
    if length <= 0:
        return {}
    body = handler.rfile.read(length)
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


class BridgeState:
    def __init__(self, fallback_image: Path) -> None:
        self._lock = threading.Lock()
        self._fallback_image = fallback_image
        self._tasks: list[dict] = []
        self._plugin_enabled = False
        self._last_heartbeat: datetime | None = None
        self._last_result: dict | None = None

    def _status_unlocked(self) -> dict:
        heartbeat_age = None
        if self._last_heartbeat is not None:
            heartbeat_age = max(0, int((datetime.now(timezone.utc) - self._last_heartbeat).total_seconds()))
        return {
            "ok": True,
            "plugin_enabled": self._plugin_enabled,
            "heartbeat_age_seconds": heartbeat_age,
            "heartbeat_recent": heartbeat_age is not None and heartbeat_age <= 180,
            "pending_count": len(self._tasks),
            "last_result": self._last_result,
        }

    def status(self) -> dict:
        with self._lock:
            return self._status_unlocked()

    def set_plugin_enabled(self, enabled: bool) -> dict:
        with self._lock:
            self._plugin_enabled = bool(enabled)
            return self._status_unlocked()

    def heartbeat(self, enabled: bool | None = None) -> dict:
        with self._lock:
            if enabled is not None:
                self._plugin_enabled = bool(enabled)
            self._last_heartbeat = datetime.now(timezone.utc)
            return self._status_unlocked()

    def queue_search(self, image_path_raw: str | None) -> dict:
        image_path = Path(image_path_raw).expanduser().resolve() if image_path_raw else self._fallback_image
        payload = _read_image_as_payload(image_path)
        task = {
            "task_id": uuid.uuid4().hex,
            "created_at": _bj_now(),
            **payload,
        }
        with self._lock:
            self._tasks.append(task)
            pending_count = len(self._tasks)
        _log(f"[queue] 新增任务 task_id={task['task_id']} image={image_path}")
        return {"ok": True, "task_id": task["task_id"], "pending_count": pending_count}

    def next_task(self) -> dict:
        with self._lock:
            if not self._tasks:
                return {"ok": True, "has_task": False}
            task = self._tasks.pop(0)
            pending_count = len(self._tasks)
        _log(f"[queue] 下发任务 task_id={task['task_id']} pending={pending_count}")
        return {"ok": True, "has_task": True, "task": task, "pending_count": pending_count}

    def set_result(self, payload: dict) -> dict:
        with self._lock:
            self._last_result = {
                "time": _bj_now(),
                "status": payload.get("status", ""),
                "url": payload.get("url", ""),
                "task_id": payload.get("task_id", ""),
                "note": payload.get("note", ""),
            }
            return {"ok": True}


class _BridgeHandler(BaseHTTPRequestHandler):
    image_path: Path = DEFAULT_IMAGE_PATH
    state = BridgeState(DEFAULT_IMAGE_PATH)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        _log(f"[http] {self.address_string()} {format % args}")

    def do_OPTIONS(self) -> None:  # noqa: N802
        _json_response(self, 200, {"ok": True})

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/health", "/healthz"):
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "service": "chrome_extension_bridge",
                    "time": _bj_now(),
                    "image_path": str(self.image_path),
                },
            )
            return

        if self.path == "/status":
            _json_response(self, 200, self.state.status())
            return

        if self.path.startswith("/frame"):
            try:
                frame = _read_image_as_payload(self.image_path)
            except Exception as exc:
                _json_response(self, 500, {"ok": False, "error": str(exc)})
                return
            _json_response(self, 200, {"ok": True, **frame})
            return

        _json_response(self, 404, {"ok": False, "error": f"unknown path: {self.path}"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = _read_json_body(self)
        except Exception as exc:
            _json_response(self, 400, {"ok": False, "error": f"invalid json: {exc}"})
            return

        if self.path == "/plugin-enabled":
            enabled = bool(payload.get("enabled", False))
            status = self.state.set_plugin_enabled(enabled)
            _log(f"[plugin] 状态更新 enabled={enabled}")
            _json_response(self, 200, status)
            return

        if self.path == "/heartbeat":
            enabled_raw = payload.get("enabled")
            enabled = bool(enabled_raw) if enabled_raw is not None else None
            status = self.state.heartbeat(enabled=enabled)
            _json_response(self, 200, status)
            return

        if self.path == "/queue":
            image_path_raw = payload.get("image_path")
            try:
                result = self.state.queue_search(str(image_path_raw) if image_path_raw else None)
            except Exception as exc:
                _json_response(self, 500, {"ok": False, "error": str(exc)})
                return
            _json_response(self, 200, result)
            return

        if self.path == "/next-task":
            _json_response(self, 200, self.state.next_task())
            return

        if self.path == "/task-result":
            _json_response(self, 200, self.state.set_result(payload))
            return

        _json_response(self, 404, {"ok": False, "error": f"unknown path: {self.path}"})


def main() -> int:
    parser = argparse.ArgumentParser(description="Chrome 扩展本地桥接服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=38999, help="监听端口，默认 38999")
    parser.add_argument(
        "--image",
        default="",
        help="固定图片路径，不传则使用内置默认路径",
    )
    args = parser.parse_args()

    image_path = Path(args.image).expanduser().resolve() if args.image else DEFAULT_IMAGE_PATH
    _BridgeHandler.image_path = image_path
    _BridgeHandler.state = BridgeState(image_path)

    _log("=" * 72)
    _log("启动 Chrome 扩展桥接服务")
    _log(f"监听地址：http://{args.host}:{args.port}")
    _log(f"固定图片：{image_path}")
    if not image_path.exists():
        _log("警告：固定图片不存在，/frame 将返回错误。")

    server = ThreadingHTTPServer((args.host, args.port), _BridgeHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("收到中断信号，服务即将退出。")
    finally:
        server.server_close()
        _log("桥接服务已退出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
