"""The local launcher must never confuse two workers running the same module."""

import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    not Path("/proc/self/environ").exists(), reason="Linux /proc required"
)


@pytest.fixture
def launcher(tmp_path):
    source = (ROOT / "scripts/dashboard.sh").read_text()
    prefix = source.split('\ncase "$ACTION" in\n', 1)[0]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    helper = scripts / "launcher-functions.sh"
    helper.write_text(prefix + '\n"$@"\n')
    return helper


@pytest.fixture
def processes():
    running = []

    def start(module, role):
        env = dict(os.environ)
        env.pop("TASK_WORKER_ROLE", None)
        if role is not None:
            env["TASK_WORKER_ROLE"] = role
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)", module], env=env
        )
        running.append(process)
        return process

    yield start
    for process in running:
        process.terminate()
        process.wait(timeout=5)


def run(launcher, *arguments):
    return subprocess.run(
        ["bash", str(launcher), *map(str, arguments)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()


def test_same_module_workers_are_discovered_by_role(launcher, processes):
    module = "worker-role-fixture-" + uuid4().hex
    ai = processes(module, "ai")
    general = processes(module, "general")
    assert run(launcher, "find_python_module_pid", module, "general") == str(
        general.pid
    )
    assert run(launcher, "find_python_module_pid", module, "ai") == str(ai.pid)


def test_default_role_is_general_and_environment_is_not_printed(launcher, processes):
    module = "worker-default-fixture-" + uuid4().hex
    general = processes(module, None)
    assert run(launcher, "find_python_module_pid", module, "general") == str(
        general.pid
    )
    assert run(launcher, "find_python_module_pid", module, "ai") == ""


def test_stale_pid_file_cannot_stop_the_other_role(launcher, processes, tmp_path):
    ai = processes("src.services.task_worker", "ai")
    pid_file = tmp_path / "general-worker.pid"
    pid_file.write_text(str(ai.pid))
    # Disable global discovery for this test: only the intentionally wrong
    # PID file is under test, and no real service may be adopted or signalled.
    source = launcher.read_text().rsplit('\n"$@"\n', 1)[0]
    launcher.write_text(source + '\nfind_managed_service_pid() { :; }\n"$@"\n')
    assert (
        run(launcher, "stop_one", "General worker", pid_file, "worker")
        == "General worker is not running"
    )
    assert ai.poll() is None
    assert not pid_file.exists()
