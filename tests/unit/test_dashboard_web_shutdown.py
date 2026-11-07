"""Exercise shutdown against long HTTP requests without touching the live port."""
from __future__ import annotations

import http.client
import http.server
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR = ROOT / "scripts/dashboard_web_supervisor.cjs"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start(command: list[str], env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [NODE, str(SUPERVISOR), *command], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )


def _wait_port(process: subprocess.Popen, port: int) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(f"server exited early: {stdout}\n{stderr}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    pytest.fail("server did not start on the isolated port")


def _cleanup(process: subprocess.Popen) -> None:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


@pytest.fixture
def simulated_server(tmp_path):
    path = tmp_path / "server.cjs"
    path.write_text('''
const http = require('node:http');
const server = http.createServer((req, res) => {
  res.writeHead(200, {'content-type': 'text/plain'});
  res.write(`starting:${process.env.NODE_OPTIONS}\\n`);
  if (process.env.TEST_MODE === 'finite') setTimeout(() => res.end('finished'), 350);
});
server.listen(Number(process.env.PORT), '127.0.0.1');
let stopping = false;
for (const signal of ['SIGTERM', 'SIGINT']) {
  process.on(signal, () => {
    if (stopping) return;
    stopping = true;
    if (process.env.TEST_MODE === 'blocked') while (true) {};
    server.close(() => process.exit(signal === 'SIGTERM' ? 143 : 130));
  });
}
''')
    return path


@pytest.mark.parametrize("mode,group_signal", [
    ("finite", True), ("finite", False), ("stream", True), ("blocked", True),
])
def test_supervisor_drains_or_bounds_active_requests(simulated_server, mode, group_signal):
    port = _free_port()
    env = {**os.environ, "PORT": str(port), "TEST_MODE": mode,
           "GLOBALID_DASHBOARD_SHUTDOWN_TIMEOUT_MS": "1000" if mode == "finite" else "400",
           "NODE_OPTIONS": "--max-old-space-size=1536"}
    process = _start([NODE, str(simulated_server)], env)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        _wait_port(process, port)
        connection.request("GET", "/", headers={"Connection": "close"} if mode == "finite" else {})
        response = connection.getresponse()
        assert response.status == 200
        assert b"starting:--max-old-space-size=1536" in response.readline()
        start = time.monotonic()
        # systemd normally signals the entire control group; direct callers
        # may signal only the supervisor and need the signal forwarded.
        (os.killpg if group_signal else os.kill)(process.pid, signal.SIGTERM)
        if mode == "finite":
            assert response.read() == b"finished"
        assert process.wait(timeout=4) == 0
        elapsed = time.monotonic() - start
        stderr = process.stderr.read()
        if mode == "finite":
            assert "shutdown exceeded" not in stderr
        else:
            assert 0.3 <= elapsed < 3
            assert "shutdown exceeded 400 ms" in stderr
    finally:
        connection.close()
        _cleanup(process)


def test_unexpected_server_failure_remains_failure():
    env = {**os.environ, "GLOBALID_DASHBOARD_SHUTDOWN_TIMEOUT_MS": "400"}
    process = _start([NODE, "-e", "process.exit(17)"], env)
    try:
        assert process.wait(timeout=5) == 17
    finally:
        _cleanup(process)


def test_invalid_shutdown_timeout_fails_before_server_start():
    env = {**os.environ, "GLOBALID_DASHBOARD_SHUTDOWN_TIMEOUT_MS": "20000"}
    process = _start([NODE, "-e", "process.exit(0)"], env)
    try:
        assert process.wait(timeout=5) == 1
        assert "between 100 and 18000" in process.stderr.read()
    finally:
        _cleanup(process)


@pytest.mark.parametrize("standalone,needs_build", [(True, False), (False, False), (True, True)])
def test_service_launcher_starts_supervised_server_in_both_modes(tmp_path, standalone, needs_build):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("service_common.sh", "run_dashboard_web_service.sh", "dashboard_web_supervisor.cjs"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    dashboard = tmp_path / "dashboard"
    for name in ("src", "public", "node_modules", ".next/static"):
        (dashboard / name).mkdir(parents=True, exist_ok=True)
    (tmp_path / "shared").mkdir()
    server = dashboard / (".next/standalone/server.js" if standalone else "node_modules/next/dist/bin/next")
    server.parent.mkdir(parents=True, exist_ok=True)
    server.write_text('''
const http = require('node:http');
const args = process.argv;
const port = process.env.PORT || args[args.indexOf('--port') + 1];
const server = http.createServer((req, res) => res.end(process.env.NODE_OPTIONS));
server.listen(Number(port), '127.0.0.1');
process.on('SIGTERM', () => server.close(() => process.exit(143)));
''')
    preload_log = tmp_path / "preload.log"
    preload = tmp_path / "instrument.cjs"
    preload.write_text("require('node:fs').appendFileSync(process.env.TEST_PRELOAD_LOG, 'loaded\\n');\n")
    node_options = f"--max-old-space-size=1536 --require={preload}"
    # In the build scenario a fake npm records its environment and creates the
    # marker; no real project build or production artifact is touched.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    build_log = tmp_path / "build-options.log"
    fake_npm = fake_bin / "npm"
    fake_npm.write_text('#!/bin/sh\nprintf %s "$NODE_OPTIONS" > "$TEST_BUILD_OPTIONS_LOG"\nprintf test-build > .next/BUILD_ID\n')
    fake_npm.chmod(0o755)
    if not needs_build:
        # Created last so the launcher can reuse the isolated test build.
        (dashboard / ".next/BUILD_ID").write_text("test-build")
    port = _free_port()
    env = {**os.environ, "GLOBALID_DASHBOARD_PORT": str(port),
           "GLOBALID_DASHBOARD_HOST": "127.0.0.1",
           "GLOBALID_DASHBOARD_SHUTDOWN_TIMEOUT_MS": "1000",
           "GLOBALID_DASHBOARD_BUILD_ON_START": "0", "TEST_PRELOAD_LOG": str(preload_log),
           "NODE_OPTIONS": node_options, "TEST_BUILD_OPTIONS_LOG": str(build_log),
           "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}
    env.pop("PORT", None)
    process = subprocess.Popen(
        [str(scripts / "run_dashboard_web_service.sh")], cwd=tmp_path, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        _wait_port(process, port)
        connection.request("GET", "/", headers={"Connection": "close"})
        response = connection.getresponse()
        assert response.status == 200
        assert response.read() == node_options.encode()
        # User-supplied preload hooks run only in the actual server, not again
        # in the supervisor (which could also contend for a debugger port).
        assert preload_log.read_text().splitlines() == ["loaded"]
        if needs_build:
            assert build_log.read_text() == node_options
        else:
            assert not build_log.exists()
        # A normal operator signal targets only the main process.
        os.kill(process.pid, signal.SIGTERM)
        assert process.wait(timeout=4) == 0
        assert "shutdown exceeded" not in process.stderr.read()
    finally:
        connection.close()
        _cleanup(process)


def test_real_standalone_event_stream_shutdown_when_build_available():
    entry = ROOT / "dashboard/.next/standalone/dashboard/server.js"
    if not entry.is_file():
        pytest.skip("standalone dashboard build is not available")
    finish = threading.Event()

    class StreamHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"data: ready\n\n")
            self.wfile.flush()
            finish.wait(10)

        def log_message(self, *_args):
            pass

    upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), StreamHandler)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    port = _free_port()
    env = {**os.environ, "PORT": str(port), "HOSTNAME": "127.0.0.1",
           "API_PROXY_TARGET": f"http://127.0.0.1:{upstream.server_port}",
           "DASHBOARD_PANEL_PASSWORD": "", "DASHBOARD_API_KEY": "",
           "GLOBALID_DASHBOARD_SHUTDOWN_TIMEOUT_MS": "400"}
    process = _start([NODE, str(entry)], env)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        _wait_port(process, port)
        connection.request("GET", "/api/v1/events/stream")
        response = connection.getresponse()
        assert response.status == 200
        assert response.read(1) == b"d"
        os.killpg(process.pid, signal.SIGTERM)
        assert process.wait(timeout=5) == 0
        assert "shutdown exceeded 400 ms" in process.stderr.read()
    finally:
        finish.set()
        connection.close()
        _cleanup(process)
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)
