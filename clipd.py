#!/usr/bin/env python3
"""Tiny clipboard bridge for the laptop <-> Pi KVM setup.

GET  /clip -> current Wayland clipboard as text/plain (204 if empty/non-text)
POST /clip -> body becomes the Wayland clipboard

Binds 0.0.0.0:8765. Reachability is governed by ufw: the wire subnet
(172.16.99.0/24 on eth0) and tailscale0 are blanket-allowed; the hostel LAN
has no rule for 8765 so it's denied by default policy. Do NOT open 8765 to
wlan0 — the clipboard is sensitive.

Runs as systemd unit `clipd` (User=screenrpi, wayland env in the unit).
"""
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BYTES = 5 * 1024 * 1024  # refuse absurd payloads


def wl(args, data=None, timeout=5):
    return subprocess.run(args, input=data, capture_output=True, timeout=timeout)


def wl_copy(data, timeout=5):
    # wl-copy forks a child that lives on to serve the clipboard; capturing its
    # output would block on the inherited pipes until that child dies, so send
    # output to devnull and only wait for the short-lived parent.
    return subprocess.run(
        ["wl-copy"], input=data, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=timeout,
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _plain(self, code, body=b""):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if self.path != "/clip":
            return self._plain(404)
        try:
            r = wl(["wl-paste", "-n", "-t", "text"])
        except subprocess.TimeoutExpired:
            return self._plain(500)
        if r.returncode != 0 or not r.stdout:
            return self._plain(204)
        self._plain(200, r.stdout)

    def do_POST(self):
        if self.path != "/clip":
            return self._plain(404)
        n = int(self.headers.get("Content-Length", 0))
        if n <= 0 or n > MAX_BYTES:
            return self._plain(413 if n > MAX_BYTES else 400)
        body = self.rfile.read(n)
        try:
            r = wl_copy(body)
        except subprocess.TimeoutExpired:
            return self._plain(500)
        self._plain(200 if r.returncode == 0 else 500)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8765), Handler).serve_forever()
