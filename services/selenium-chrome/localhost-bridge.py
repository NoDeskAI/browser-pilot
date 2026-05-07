#!/usr/bin/env python3
"""Session-local TCP bridge for localhost development targets.

The browser runs inside the Selenium container, so http://localhost:PORT points
at the container, not the developer machine.  This bridge listens inside the
container and forwards TCP bytes to host.docker.internal:PORT.  It is protocol
agnostic, so HTTP, HTTPS, and WebSocket upgrades share the same path.
"""

from __future__ import annotations

import argparse
import json
import os
import selectors
import signal
import socket
import sys
import time
from pathlib import Path


def _pid_alive(path: Path) -> bool:
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _emit(**payload) -> None:
    print(json.dumps(payload, separators=(",", ":")), flush=True)


def _relay(left: socket.socket, right: socket.socket) -> None:
    selector = selectors.DefaultSelector()
    left.setblocking(False)
    right.setblocking(False)
    selector.register(left, selectors.EVENT_READ, right)
    selector.register(right, selectors.EVENT_READ, left)
    try:
        while True:
            events = selector.select(timeout=60)
            if not events:
                return
            for key, _mask in events:
                src = key.fileobj
                dst = key.data
                try:
                    data = src.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                try:
                    dst.sendall(data)
                except OSError:
                    return
    finally:
        selector.close()
        left.close()
        right.close()


def _serve(args) -> None:
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    pid_path = Path(args.pid_file)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()), encoding="utf-8")

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.listen_host, args.listen_port))
    server.listen(128)
    try:
        while True:
            client, _addr = server.accept()
            try:
                upstream = socket.create_connection((args.target_host, args.target_port), timeout=10)
            except OSError:
                client.close()
                continue
            pid = os.fork()
            if pid == 0:
                server.close()
                _relay(client, upstream)
                os._exit(0)
            client.close()
            upstream.close()
    finally:
        server.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--target-host", default="host.docker.internal")
    parser.add_argument("--target-port", type=int, required=True)
    parser.add_argument("--pid-file", required=True)
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--daemon", action="store_true")
    args = parser.parse_args()

    pid_path = Path(args.pid_file)
    if _pid_alive(pid_path):
        _emit(status="already_running", port=args.listen_port, pid=int(pid_path.read_text(encoding="utf-8")))
        return 0

    if args.daemon:
        pid = os.fork()
        if pid:
            time.sleep(0.2)
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                _emit(status="failed", port=args.listen_port, error=f"bridge child exited with status {status}")
                return 1
            _emit(status="started", port=args.listen_port, pid=pid)
            return 0
        os.setsid()
        log = open(args.log_file, "a", encoding="utf-8")
        os.dup2(log.fileno(), sys.stdout.fileno())
        os.dup2(log.fileno(), sys.stderr.fileno())

    try:
        _serve(args)
    except Exception as exc:
        _emit(status="failed", port=args.listen_port, error=str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
