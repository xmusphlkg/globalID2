import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "cloudflare" / "subscriptions" / "scripts" / "wrangler-env.sh"


def test_wrangler_env_loads_shell_sensitive_dotenv_without_eval(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    script = repo / "cloudflare" / "subscriptions" / "scripts" / "wrangler-env.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(SCRIPT, script)

    python_link = repo / "venv" / "bin" / "python3"
    python_link.parent.mkdir(parents=True)
    python_link.symlink_to(sys.executable)

    (repo / ".env").write_text(
        "\n".join(
            [
                "CLOUDFLARE_API_TOKEN=test-token",
                "SUBSCRIPTIONS__D1_DATABASE_NAME=test-db",
                "SUBSCRIPTIONS__D1_DATABASE_ID=test-id",
                "SUBSCRIPTIONS__WORKER_NAME=from-dotenv (production)",
                "DATA_RELEASE__DATA_REFRESH_SNAPSHOT_MESSAGE_TEMPLATE=chore(data): {timestamp}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["CLOUDFLARE_API_TOKEN"] = "process-token"
    env["SUBSCRIPTIONS__WORKER_NAME"] = "from-process"
    completed = subprocess.run(
        ["bash", str(script), "config-path"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    generated = repo / "cloudflare" / "subscriptions" / "wrangler.generated.toml"
    content = generated.read_text(encoding="utf-8")
    assert 'name = "from-process"' in content
    assert 'database_name = "test-db"' in content
    assert 'database_id = "test-id"' in content
    assert "syntax error" not in completed.stderr.lower()
