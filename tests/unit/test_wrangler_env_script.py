import json
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
                "SUBSCRIPTIONS__D1_DATABASE_ID=00000000-0000-4000-8000-000000000001",
                "SUBSCRIPTIONS__PUBLIC_BASE_URL=https://subscriptions.example.invalid",
                "SUBSCRIPTIONS__ALLOWED_ORIGINS=https://www.example.invalid",
                "SUBSCRIPTIONS__WORKERS_DEV=false",
                "SUBSCRIPTIONS__WORKER_NAME=from-dotenv (production)",
                "UNRELATED_MESSAGE_TEMPLATE=chore(data): {timestamp}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["CLOUDFLARE_API_TOKEN"] = "process-token"
    env["SUBSCRIPTIONS__WORKER_NAME"] = "from-process"
    completed = subprocess.run(
        ["bash", str(script), "config-path", "production"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    generated = repo / "cloudflare" / "subscriptions" / "wrangler.generated.jsonc"
    content = json.loads(generated.read_text(encoding="utf-8"))
    assert content["env"]["production"]["name"] == "from-process"
    assert content["env"]["production"]["d1_databases"][0]["database_name"] == "test-db"
    assert content["env"]["production"]["d1_databases"][0]["database_id"] == "00000000-0000-4000-8000-000000000001"
    assert "syntax error" not in completed.stderr.lower()
