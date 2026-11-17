"""Integration test for the vendarix-hw-adapter C++ UDS daemon.

Skipped automatically when g++ or a Linux socket stack is unavailable (the
service targets Linux + systemd). Compiles the daemon, stands up a fake
"edge" UDS HTTP server, and verifies ping / event forwarding / heartbeat
forwarding / unknown-command handling end to end.
"""

import json
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "hardware_adapter.cpp"


def _gxx():
    return shutil.which("g++") or shutil.which("c++") or shutil.which("clang++")


pytestmark = pytest.mark.skipif(
    platform.system() != "Linux" or _gxx() is None,
    reason="hardware-adapter targets Linux with a C++20 compiler",
)


@pytest.fixture()
def build_dir(tmp_path):
    return tmp_path


@pytest.fixture()
def adapter(build_dir):
    binary = build_dir / "vendarix-hw-adapter"
    if not binary.exists():
        subprocess.run(
            [_gxx(), "-std=c++20", "-O1", "-Wall", "-Wextra", "-Werror", "-pedantic",
             "-o", str(binary), str(SOURCE), "-pthread"],
            check=True,
            capture_output=True,
            text=True,
        )
    return binary


class FakeEdge:
    """Minimal HTTP/1.1-over-UDS server that records POST paths and bodies."""

    def __init__(self, sock_path: Path):
        self.sock_path = sock_path
        self.requests = []
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        if self.sock_path.exists():
            self.sock_path.unlink()
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(self.sock_path))
        self._server.listen(4)
        self._server.settimeout(0.2)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._server.close()
        if self.sock_path.exists():
            self.sock_path.unlink()

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        with conn:
            conn.settimeout(2.0)
            data = b""
            try:
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(8192)
                    if not chunk:
                        break
                    data += chunk
                head, _, body = data.partition(b"\r\n\r\n")
                headers = dict(
                    line.split(":", 1) for line in head.decode().split("\r\n")[1:] if ":" in line
                )
                length = int(headers.get("Content-Length", headers.get("content-length", "0")))
                while len(body) < length:
                    chunk = conn.recv(8192)
                    if not chunk:
                        break
                    body += chunk
                path = head.decode().split(" ", 2)[1]
                self.requests.append((path, body.decode()))
                response = json.dumps({"status": "received", "route": path}).encode()
                conn.sendall(
                    b"HTTP/1.1 201 Created\r\nContent-Type: application/json\r\n"
                    b"Content-Length: " + str(len(response)).encode() + b"\r\nConnection: close\r\n\r\n" + response
                )
            except (socket.timeout, ValueError):
                pass


def _uds_send(sock_path: Path, payload: str, timeout=5) -> str:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(sock_path))
        client.sendall((payload.rstrip("\n") + "\n").encode())
        chunks = []
        while True:
            chunk = client.recv(8192)
            if not chunk:
                break
            chunks.append(chunk)
            if chunk.endswith(b"\n"):
                break
        return b"".join(chunks).decode()
    finally:
        client.close()


def test_adapter_round_trip(adapter, build_dir):
    listen = build_dir / "hw-adapter.sock"
    edge = build_dir / "edge.sock"
    with FakeEdge(edge) as fake:
        proc = subprocess.Popen([str(adapter), "--listen", str(listen), "--edge-uds", str(edge)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            for _ in range(50):
                if listen.exists():
                    break
                time.sleep(0.05)
            assert listen.exists(), "daemon did not create its socket"

            pong = json.loads(_uds_send(listen, '{"cmd":"ping"}'))
            assert pong == {"ok": True, "daemon": "vendarix-hw-adapter", "version": "1.0.0"}

            result = json.loads(_uds_send(listen, json.dumps({
                "cmd": "event",
                "vendor": "ncr-voyix",
                "device_id": "ncr-lane-ci-1",
                "source_seq": 7,
                "raw": {"msgType": "TX_COMPLETE", "txnId": "CI-T-1", "total": 1.0},
            })))
            assert result["ok"] is True and result["forwarded"] is True
            assert result["status"] == 201
            assert fake.requests, "edge received nothing"
            path, body = fake.requests[-1]
            assert path == "/api/v1/edge/ingest"
            forwarded = json.loads(body)
            assert forwarded["vendor"] == "ncr-voyix"
            assert forwarded["raw"]["txnId"] == "CI-T-1"

            hb = json.loads(_uds_send(listen, json.dumps({
                "cmd": "heartbeat", "device_id": "ncr-lane-ci-1",
                "vendor": "ncr-voyix", "interface": "ncr-voyix/pos-v1", "healthy": True,
            })))
            assert hb["ok"] and hb["forwarded"]
            path, _ = fake.requests[-1]
            assert path == "/api/v1/edge/devices/heartbeat"

            bad = json.loads(_uds_send(listen, '{"cmd":"bogus"}'))
            assert bad["ok"] is False
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def test_adapter_version_smoke(adapter):
    out = subprocess.run([str(adapter), "--version"], capture_output=True, text=True, timeout=5, check=True)
    assert out.stdout.strip() == "1.0.0"
