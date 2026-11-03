# hatenna_lite/console.py — the demo console.
#
# A separate process from the bot, on purpose: it can start the bot, stop it
# gracefully, or kill -9 it, and keep showing what's on disk while the bot is
# down. It only ever talks to the bot through files: it appends events to the
# inbox and reads status.json, feed.jsonl and ledger.jsonl.
from __future__ import annotations

import json
import os
import random
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from .bot import Paths, append_feed, default_var

STATIC = Path(__file__).parent / "static"
SPAWNS = [  # what the image model would say for each demo spawn
    ("Dratini", 0.97), ("Bagon", 0.93), ("Finizen", 0.61), ("Eevee", 0.99),
    ("Gible", 0.95), ("Larvitar", 0.88), ("Magikarp", 0.99), ("Riolu", 0.91),
]


class Console:
    def __init__(self, paths: Paths):
        self.paths = paths
        self.proc: Optional[subprocess.Popen] = None
        self.last_exit: Optional[str] = None
        self.lock = threading.Lock()

    # --- bot process control ---
    def _external_pid(self) -> Optional[int]:
        """A bot this console did not start (e.g. run from a terminal)."""
        try:
            status = json.loads(self.paths.status.read_text())
            os.kill(status["pid"], 0)
            if time.time() - status["heartbeat"] < 5:
                return status["pid"]
        except Exception:
            pass
        return None

    def running_pid(self) -> Optional[int]:
        if self.proc and self.proc.poll() is None:
            return self.proc.pid
        if self.proc and self.proc.poll() is not None and self.last_exit is None:
            code = self.proc.returncode
            self.last_exit = "killed (SIGKILL)" if code == -signal.SIGKILL else f"exited ({code})"
        return self._external_pid()

    def start(self) -> str:
        with self.lock:
            if self.running_pid():
                return "already running"
            self.paths.var.mkdir(parents=True, exist_ok=True)
            log = (self.paths.var / "bot.log").open("a")
            env = {**os.environ, "HATENNA_LITE_VAR": str(self.paths.var), "PYTHONUNBUFFERED": "1"}
            self.proc = subprocess.Popen([sys.executable, "-m", "hatenna_lite", "bot"],
                                         cwd=str(Path(__file__).parent.parent),
                                         stdout=log, stderr=subprocess.STDOUT, env=env)
            self.last_exit = None
            return f"started pid {self.proc.pid}"

    def signal_bot(self, sig: int) -> str:
        with self.lock:
            pid = self.running_pid()
            if not pid:
                return "not running"
            os.kill(pid, sig)
            label = "kill -9" if sig == signal.SIGKILL else "graceful stop"
            append_feed(self.paths, "console", f"{label} sent to pid {pid}")
            return label

    def reset(self) -> str:
        with self.lock:
            if self.running_pid():
                return "stop the bot first"
            shutil.rmtree(self.paths.var, ignore_errors=True)
            self.last_exit = None
            return "demo reset"

    # --- inputs: append to the inbox, exactly like any other event source ---
    def _append(self, event: dict) -> dict:
        self.paths.var.mkdir(parents=True, exist_ok=True)
        with self.paths.inbox.open("a") as f:
            f.write(json.dumps(event) + "\n")
        return event

    def _last_event(self) -> Optional[dict]:
        if not self.paths.inbox.exists():
            return None
        lines = self.paths.inbox.read_text().splitlines()
        return json.loads(lines[-1]) if lines else None

    def send(self, body: dict) -> dict:
        kind = body.get("kind")
        if kind == "replay":
            last = self._last_event()
            return self._append(last) if last else {"error": "nothing to replay"}
        n = self._inbox_lines() + 1
        if kind == "spawn":
            name, conf = random.choice(SPAWNS) if not body.get("pokemon") else \
                next(((s, c) for s, c in SPAWNS if s.lower() == body["pokemon"].lower()),
                     (body["pokemon"].title(), 0.9))
            return self._append({"id": f"s-{n:03d}", "kind": "spawn",
                                 "payload": {"model_guess": name, "confidence": conf}})
        if kind == "burst":
            return {"sent": [self.send({"kind": "spawn"})["id"] for _ in range(int(body.get("n", 5)))]}
        if kind == "hunt":
            return self._append({"id": f"h-{n:03d}", "kind": "hunt", "payload": {
                "user": body.get("user") or "kaushik", "pokemon": body.get("pokemon") or "dratini"}})
        if kind == "ask":
            return self._append({"id": f"q-{n:03d}", "kind": "ask", "payload": {
                "user": body.get("user") or "judge", "text": body.get("text") or "how are you doing?"}})
        if kind == "chaos":
            return self._append({"id": f"c-{uuid.uuid4().hex[:6]}", "kind": "chaos",
                                 "payload": {"target": body.get("target", "pinger")}})
        return {"error": f"unknown kind {kind!r}"}

    def _inbox_lines(self) -> int:
        if not self.paths.inbox.exists():
            return 0
        with self.paths.inbox.open("rb") as f:
            return sum(1 for _ in f)

    # --- the state the page polls ---
    def state(self) -> dict:
        status = None
        if self.paths.status.exists():
            try:
                status = json.loads(self.paths.status.read_text())
            except ValueError:
                pass
        backlog = 0
        if self.paths.inbox.exists():
            with self.paths.inbox.open("rb") as f:
                f.seek(status["cursor"] if status else 0)
                backlog = f.read().count(b"\n")
        pid = self.running_pid()
        return {
            "now": time.time(),
            "bot": {"running": bool(pid), "pid": pid, "last_exit": None if pid else self.last_exit},
            "status": status,
            "backlog": backlog,
            "feed": _tail_jsonl(self.paths.feed, 200),
            "ledger": _tail_jsonl(self.paths.ledger, 8),
        }


def _tail_jsonl(path: Path, n: int) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines()[-n:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def make_handler(console: Console):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # keep the terminal quiet
            pass

        def _json(self, obj, code: int = 200) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/api/state":
                return self._json(console.state())
            if self.path in ("/", "/index.html"):
                body = (STATIC / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json({"error": "bad json"}, 400)
            actions = {
                "/api/bot/start": lambda: {"result": console.start()},
                "/api/bot/stop": lambda: {"result": console.signal_bot(signal.SIGTERM)},
                "/api/bot/kill": lambda: {"result": console.signal_bot(signal.SIGKILL)},
                "/api/reset": lambda: {"result": console.reset()},
                "/api/event": lambda: console.send(body),
            }
            if self.path not in actions:
                return self._json({"error": "not found"}, 404)
            self._json(actions[self.path]())

    return Handler


def main(port: int = 8765, autostart: bool = True) -> None:
    console = Console(Paths(default_var()))
    if autostart:
        console.start()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(console))
    print(f"Hatenna Lite demo console → http://127.0.0.1:{port}  (Ctrl-C to quit)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if console.proc and console.proc.poll() is None:
            console.proc.terminate()
