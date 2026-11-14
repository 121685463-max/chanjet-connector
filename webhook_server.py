#!/usr/bin/env python3
"""chanjet-connector GitHub webhook 接收端（push 即上线）。

监听 127.0.0.1:8092，校验 GitHub 推送签名(X-Hub-Signature-256)，
签名通过且是 main 分支 push 时，后台触发 deploy.sh 做「git pull + docker build + 重启」。

鉴权：HMAC-SHA256(secret, request_body) 与 X-Hub-Signature-256 常量时间比对。
secret 由 systemd EnvironmentFile 注入环境变量 WEBHOOK_SECRET，不落仓库。
"""

import hashlib
import hmac
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 8092
SECRET = os.environ.get("WEBHOOK_SECRET", "").encode()
DEPLOY_SCRIPT = "/opt/chanjet-connector/deploy.sh"
LOG_DIR = "/var/log/chanjet-connector-webhook"
EXPECTED_PATH = "/webhook/chanjet-connector"


def verify_signature(body: bytes, signature: str) -> bool:
    """校验 X-Hub-Signature-256=sha256=<hex>，无 secret 或缺签名一律拒绝。"""
    if not SECRET or not signature:
        return False
    expected = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else b""

        path = self.path.split("?", 1)[0].rstrip("/")
        if path != EXPECTED_PATH:
            self._reply(404, "not found")
            return

        if not verify_signature(body, self.headers.get("X-Hub-Signature-256", "")):
            self._reply(403, "invalid signature")
            return

        try:
            payload = json.loads(body or b"{}")
        except Exception:
            self._reply(400, "bad json")
            return

        # 只响应 main 分支 push；ping 事件无 ref，忽略但不报错
        if payload.get("ref", "") != "refs/heads/main":
            self._reply(200, "ignored: not main push")
            return

        self._spawn_deploy()
        self._reply(202, "deploy triggered")

    def do_GET(self):
        self._reply(200, "chanjet-connector-webhook ok")

    def _spawn_deploy(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        log_path = os.path.join(LOG_DIR, "deploy.log")
        with open(log_path, "a") as logf:
            subprocess.Popen(
                ["bash", DEPLOY_SCRIPT],
                stdout=logf,
                stderr=logf,
                stdin=subprocess.DEVNULL,
                start_new_session=True,  # 脱离 webhook 进程，防被回收
            )

    def _reply(self, code: int, msg: str):
        data = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        # 访问日志交给 systemd journal，不刷到 stderr
        pass


def main():
    if not SECRET:
        raise SystemExit("WEBHOOK_SECRET 未设置，拒绝裸奔启动")
    os.makedirs(LOG_DIR, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
