#!/usr/bin/env python3
"""
模拟「网页服务端」与设备直接对接的两个 HTTP 端点，严格按《软硬件对接文档》第7节实现：
  - POST /api/camera/latest   设备上传 JPEG 快照（需 x-device-token: <DEVICE_TOKEN>）
  - GET  /api/camera/latest   拉取最新快照 / 空态
  - GET  /api/iot/state       返回 daemon 镜像进 Redis 的最新设备状态
内置一个内存 Redis 等价物（bag:latest），由 daemon_mirror.py 通过 HTTP 写入。
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json, os, threading, time, sys

DEVICE_TOKEN = os.environ.get("BAG_DEVICE_TOKEN", "")
_lock = threading.Lock()
_state = {
    "status": None, "deviceOnline": False,
    "battery": None, "temp": None, "humid": None,
    "lat": None, "lng": None, "lastSeenAt": None,
}
_snapshot = {"bytes": None, "ts": None}

def set_state(**kw):
    with _lock:
        _state.update(kw)

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/api/camera/latest":
            token = self.headers.get("x-device-token")
            if token != DEVICE_TOKEN:
                return self._json(401, {"success": False, "message": "Unauthorized Device"})
            ctype = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            if "multipart/form-data" not in ctype or b"name=\"image\"" not in raw:
                return self._json(400, {"success": False, "message": "未找到图片文件"})
            # 抽取 JPEG 部分（boundary 之间）
            with _lock:
                _snapshot["bytes"] = len(raw)
                _snapshot["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
            return self._json(200, {"success": True, "message": "快照上传成功",
                                    "timestamp": _snapshot["ts"]})
        # daemon 内部写状态
        if self.path == "/_internal/state":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
            set_state(**data)
            return self._json(200, {"ok": True})
        self._json(404, {"error": "not found"})

    def do_GET(self):
        if self.path == "/api/camera/latest":
            with _lock:
                if _snapshot["bytes"]:
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(_snapshot["bytes"]))
                    self.end_headers()
                    self.wfile.write(b"\xff" * _snapshot["bytes"])
                    return
            return self._json(200, {"success": True, "hasSnapshot": False,
                                    "message": "暂无快照", "timestamp": None})
        if self.path in ("/api/iot/state", "/api/iot/status"):
            with _lock:
                return self._json(200, dict(_state))
        self._json(404, {"error": "not found"})

def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"[mock-web] listening on :{port}", flush=True)
    srv.serve_forever()

if __name__ == "__main__":
    main()
