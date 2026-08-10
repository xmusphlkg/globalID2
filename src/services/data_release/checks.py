"""External integration checks used by the data release orchestrator.

The functions in this module deliberately receive command/API callables from the
caller.  This keeps network and process access outside the release state machine
and makes every failure path testable without contacting external services.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
from typing import Any, Awaitable, Callable, Optional
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest


Capture = Callable[..., Awaitable[dict[str, Any]]]
JsonFetch = Callable[[str], Awaitable[dict[str, Any]]]
LatestDeployment = Callable[..., Awaitable[Optional[dict[str, Any]]]]


def is_github_ssh_repo_url(repo_url: str, prefixes: tuple[str, ...]) -> bool:
    normalized = (repo_url or "").strip().lower()
    return any(normalized.startswith(prefix) for prefix in prefixes)


def build_git_env(
    repo_url: str,
    *,
    github_ssh_prefixes: tuple[str, ...],
    use_github_ssh_over_443: bool = False,
) -> dict[str, str]:
    ssh_parts = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if use_github_ssh_over_443 and is_github_ssh_repo_url(repo_url, github_ssh_prefixes):
        ssh_parts.extend(["-o", "Hostname=ssh.github.com", "-p", "443"])
    return {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": " ".join(ssh_parts),
    }


async def raw_archive_check(
    *,
    enabled: bool,
    repo_url: str,
    source_dir: Path,
    repository_dir: Path,
    branch: str,
    root_dir: Path,
    github_ssh_prefixes: tuple[str, ...],
    run_capture: Capture,
) -> dict[str, Any]:
    payload = {
        "enabled": bool(enabled),
        "repo_url": repo_url or None,
        "branch": branch,
        "source_dir": str(source_dir),
        "repository_dir": str(repository_dir),
        "read_access_ok": False,
        "write_access_ok": False,
        "ssh_transport": "disabled" if not enabled else "default",
        "read_check_output": None,
        "write_check_output": None,
    }
    blockers: list[str] = []
    if not enabled:
        return {"payload": payload, "blockers": blockers}
    if not repo_url.strip():
        blockers.append("Missing raw archive repository URL.")
    if not source_dir.is_dir():
        blockers.append(f"Raw archive source directory is missing: {source_dir}")
    if blockers:
        return {"payload": payload, "blockers": blockers}

    git_env = build_git_env(repo_url, github_ssh_prefixes=github_ssh_prefixes)
    read = await run_capture(
        ["git", "ls-remote", repo_url, "HEAD"],
        cwd=root_dir,
        env=git_env,
        timeout=40,
    )
    if read["returncode"] != 0 and is_github_ssh_repo_url(repo_url, github_ssh_prefixes):
        fallback_env = build_git_env(
            repo_url,
            github_ssh_prefixes=github_ssh_prefixes,
            use_github_ssh_over_443=True,
        )
        fallback = await run_capture(
            ["git", "ls-remote", repo_url, "HEAD"],
            cwd=root_dir,
            env=fallback_env,
            timeout=40,
        )
        if fallback["returncode"] == 0:
            read = fallback
            git_env = fallback_env
            payload["ssh_transport"] = "github-ssh-over-443"
        else:
            read["stdout"] = (
                f"Primary SSH failed:\n{read.get('stdout') or ''}\n\n"
                f"SSH-over-443 failed:\n{fallback.get('stdout') or ''}"
            ).strip()
    payload["read_check_output"] = read["stdout"]
    payload["read_access_ok"] = read["returncode"] == 0
    if read["returncode"] != 0:
        blockers.append("Raw archive repository read check failed.")
        return {"payload": payload, "blockers": blockers}

    probe_branch = f"__globalid_write_probe__/{branch}"
    write = await run_capture(
        ["git", "push", "--dry-run", repo_url, f"HEAD:refs/heads/{probe_branch}"],
        cwd=root_dir,
        env=git_env,
        timeout=40,
    )
    payload["write_check_output"] = write["stdout"]
    payload["write_access_ok"] = write["returncode"] == 0
    if write["returncode"] != 0:
        blockers.append("Raw archive repository write check failed.")
    return {"payload": payload, "blockers": blockers}


async def git_status_paths(*, run_capture: Capture, root_dir: Path) -> list[str]:
    result = await run_capture(
        ["git", "status", "--porcelain=v1", "-uall"],
        cwd=root_dir,
    )
    if result["returncode"] != 0:
        return []
    paths: list[str] = []
    for raw_line in result["stdout"].splitlines():
        line = raw_line.rstrip()
        if len(line) < 3:
            continue
        if line[2] == " ":
            path = line[3:]
        elif line[1] == " ":
            # Capture output strips leading whitespace from the first status line.
            path = line[2:]
        else:
            continue
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if path:
            paths.append(path.replace("\\", "/"))
    return paths


async def tracked_generated_paths(
    *,
    run_capture: Capture,
    root_dir: Path,
    generated_data_paths: tuple[str, ...],
) -> list[str]:
    result = await run_capture(
        ["git", "ls-files", "--", *generated_data_paths],
        cwd=root_dir,
    )
    if result["returncode"] != 0:
        return ["<unable to verify repository boundary>"]
    return [line for line in result["stdout"].splitlines() if line.strip()]


async def download_repo_check(
    *,
    repo_url: str,
    branch: str,
    raw_base_url: str,
    root_dir: Path,
    github_ssh_prefixes: tuple[str, ...],
    run_capture: Capture,
) -> dict[str, Any]:
    git_env = build_git_env(repo_url, github_ssh_prefixes=github_ssh_prefixes)
    ssh_transport = "default"
    payload = {
        "repo_url": repo_url or None,
        "branch": branch,
        "raw_base_url": raw_base_url or None,
        "read_access_ok": False,
        "write_access_ok": False,
        "read_check_output": None,
        "write_check_output": None,
        "ssh_transport": ssh_transport,
    }
    blockers: list[str] = []
    if not repo_url:
        blockers.append("Missing GITHUB_DATA_SHARE_REPO_URL.")
        return {"payload": payload, "blockers": blockers}

    read_check: dict[str, Any] = {"returncode": 127, "stdout": "read check did not run"}
    fallback_read_output: Optional[str] = None
    for attempt in range(1, 4):
        read_check = await run_capture(
            ["git", "ls-remote", repo_url, "HEAD"],
            cwd=root_dir,
            env=git_env,
            timeout=40,
        )
        if read_check["returncode"] != 0 and is_github_ssh_repo_url(repo_url, github_ssh_prefixes):
            fallback_env = build_git_env(
                repo_url,
                github_ssh_prefixes=github_ssh_prefixes,
                use_github_ssh_over_443=True,
            )
            fallback_read = await run_capture(
                ["git", "ls-remote", repo_url, "HEAD"],
                cwd=root_dir,
                env=fallback_env,
                timeout=40,
            )
            if fallback_read["returncode"] == 0:
                read_check = fallback_read
                git_env = fallback_env
                ssh_transport = "github-ssh-over-443"
                fallback_read_output = None
            else:
                fallback_read_output = fallback_read.get("stdout") or ""
        if read_check["returncode"] == 0:
            break
        if attempt < 3:
            await asyncio.sleep(min(5, attempt * 2))

    if (
        read_check["returncode"] != 0
        and is_github_ssh_repo_url(repo_url, github_ssh_prefixes)
        and fallback_read_output is not None
    ):
        read_check["stdout"] = (
            "Primary SSH (port 22) failed:\n"
            + (read_check.get("stdout") or "")
            + "\n\nSSH-over-443 fallback failed:\n"
            + fallback_read_output
        ).strip()
    payload["read_check_output"] = read_check["stdout"]
    payload["read_access_ok"] = read_check["returncode"] == 0
    payload["ssh_transport"] = ssh_transport
    if read_check["returncode"] != 0:
        blockers.append("Download-data repo read check failed.")
        return {"payload": payload, "blockers": blockers}

    with tempfile.TemporaryDirectory(prefix="globalid-data-release-check-") as temp_dir:
        temp_path = Path(temp_dir)
        init_check = await run_capture(["git", "init"], cwd=temp_path, env=git_env, timeout=40)
        if init_check["returncode"] != 0:
            payload["write_check_output"] = init_check["stdout"]
            blockers.append("Download-data repo write check failed.")
            return {"payload": payload, "blockers": blockers}
        remote_add_check = await run_capture(
            ["git", "remote", "add", "origin", repo_url], cwd=temp_path, env=git_env, timeout=40
        )
        if remote_add_check["returncode"] != 0:
            payload["write_check_output"] = remote_add_check["stdout"]
            blockers.append("Download-data repo write check failed.")
            return {"payload": payload, "blockers": blockers}
        commit_check = await run_capture(
            [
                "git", "-c", "user.name=GlobalID Data Release", "-c",
                "user.email=noreply@globalid.local", "commit", "--allow-empty", "-m",
                "chore: permission check",
            ],
            cwd=temp_path,
            env=git_env,
            timeout=40,
        )
        if commit_check["returncode"] != 0:
            payload["write_check_output"] = commit_check["stdout"]
            blockers.append("Download-data repo write check failed.")
            return {"payload": payload, "blockers": blockers}

        probe_branch = f"__globalid_write_probe__/{branch or 'main'}"
        write_check: dict[str, Any] = {"returncode": 127, "stdout": "write check did not run"}
        for attempt in range(1, 3):
            write_check = await run_capture(
                ["git", "push", "--dry-run", "origin", f"HEAD:refs/heads/{probe_branch}"],
                cwd=temp_path,
                env=git_env,
                timeout=40,
            )
            if write_check["returncode"] == 0:
                break
            if is_github_ssh_repo_url(repo_url, github_ssh_prefixes) and ssh_transport == "default":
                fallback_env = build_git_env(
                    repo_url,
                    github_ssh_prefixes=github_ssh_prefixes,
                    use_github_ssh_over_443=True,
                )
                fallback_write = await run_capture(
                    ["git", "push", "--dry-run", "origin", f"HEAD:refs/heads/{probe_branch}"],
                    cwd=temp_path,
                    env=fallback_env,
                    timeout=40,
                )
                if fallback_write["returncode"] == 0:
                    write_check = fallback_write
                    git_env = fallback_env
                    ssh_transport = "github-ssh-over-443"
                    break
                write_check["stdout"] = (
                    "Primary SSH (port 22) failed:\n" + (write_check.get("stdout") or "")
                    + "\n\nSSH-over-443 fallback failed:\n" + (fallback_write.get("stdout") or "")
                ).strip()
            if attempt < 2:
                await asyncio.sleep(min(5, attempt * 2))
        payload["write_check_output"] = write_check["stdout"]
        payload["write_access_ok"] = write_check["returncode"] == 0
        payload["ssh_transport"] = ssh_transport
        if write_check["returncode"] != 0:
            blockers.append("Download-data repo write check failed.")
    return {"payload": payload, "blockers": blockers}


async def cloudflare_api_json(url: str, *, token: str) -> dict[str, Any]:
    request = urlrequest.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="GET",
    )
    last_exc: Optional[Exception] = None

    def fetch() -> dict[str, Any]:
        with urlrequest.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))

    for attempt in range(1, 4):
        try:
            return await asyncio.to_thread(fetch)
        except urlerror.HTTPError as exc:
            last_exc = exc
            if 400 <= exc.code < 500 and exc.code != 429:
                break
        except (urlerror.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_exc = exc
        if attempt < 3:
            await asyncio.sleep(min(5, attempt * 2))
    raise RuntimeError(str(last_exc) if last_exc else "Cloudflare API request failed")


def normalize_cloudflare_deployment(deployment: dict[str, Any]) -> dict[str, Any]:
    trigger = deployment.get("deployment_trigger") or {}
    metadata = trigger.get("metadata") or {}
    latest_stage = deployment.get("latest_stage") or {}
    return {
        "id": deployment.get("id"),
        "url": deployment.get("url"),
        "environment": deployment.get("environment"),
        "created_on": deployment.get("created_on"),
        "status": latest_stage.get("status"),
        "branch": metadata.get("branch"),
        "commit_hash": metadata.get("commit_hash"),
        "commit_message": metadata.get("commit_message"),
        "commit_dirty": bool(metadata.get("commit_dirty")),
    }


async def cloudflare_latest_deployment(
    project_name: str,
    *,
    environment: str,
    account_id: str,
    api_json: JsonFetch,
) -> Optional[dict[str, Any]]:
    query = urlparse.urlencode({"env": environment, "per_page": 1})
    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{account_id}/pages/projects/{project_name}/deployments?{query}"
    )
    body = await api_json(url)
    if not body.get("success"):
        messages = body.get("errors") or body.get("messages") or []
        raise RuntimeError(f"Cloudflare deployment lookup failed: {json.dumps(messages, ensure_ascii=False)}")
    deployments = body.get("result") or []
    if not deployments:
        return None
    return normalize_cloudflare_deployment(deployments[0])


def cloudflare_deployment_matches(
    deployment: Optional[dict[str, Any]], identity: dict[str, Any]
) -> bool:
    if not deployment:
        return False
    return (
        deployment.get("environment") == "production"
        and deployment.get("status") == "success"
        and deployment.get("branch") == identity.get("deployment_branch")
        and deployment.get("commit_hash") == identity.get("source_commit")
    )


async def cloudflare_check(
    *,
    project: str,
    token: str,
    account_id: str,
    api_json: JsonFetch,
    latest_deployment: LatestDeployment,
) -> dict[str, Any]:
    payload = {
        "project_name": project,
        "token_present": bool(token),
        "account_id_present": bool(account_id),
        "project_access_ok": False,
        "subdomain": None,
        "domains": [],
        "production_branch": None,
        "latest_production_deployment": None,
        "error": None,
    }
    blockers: list[str] = []
    if not token:
        blockers.append("Missing CLOUDFLARE_API_TOKEN.")
    if not account_id:
        blockers.append("Missing CLOUDFLARE_ACCOUNT_ID.")
    if not project:
        blockers.append("Missing Cloudflare Pages project name.")
    if blockers:
        payload["error"] = "; ".join(blockers)
        return {"payload": payload, "blockers": blockers}

    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/pages/projects/{project}"
    try:
        body = await api_json(url)
        payload["project_access_ok"] = bool(body.get("success"))
        if not payload["project_access_ok"]:
            messages = body.get("errors") or body.get("messages") or []
            raise RuntimeError(json.dumps(messages, ensure_ascii=False))
        project_data = body.get("result") or {}
        payload["subdomain"] = project_data.get("subdomain")
        payload["domains"] = list(project_data.get("domains") or [])
        payload["production_branch"] = project_data.get("production_branch")
        payload["latest_production_deployment"] = await latest_deployment(
            project, environment="production"
        )
        return {"payload": payload, "blockers": blockers}
    except RuntimeError as exc:
        payload["error"] = str(exc)
        blockers.append("Cloudflare Pages project check failed.")
    return {"payload": payload, "blockers": blockers}


async def public_json(url: str) -> dict[str, Any]:
    request = urlrequest.Request(
        url,
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
        },
        method="GET",
    )

    def fetch() -> dict[str, Any]:
        with urlrequest.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))

    return await asyncio.to_thread(fetch)


async def verify_cloudflare_production_release(
    *,
    project_name: str,
    subdomain: str,
    release_identity: dict[str, Any],
    latest_deployment: LatestDeployment,
    fetch_public_json: JsonFetch,
) -> dict[str, Any]:
    if not subdomain:
        raise RuntimeError("Cloudflare production subdomain is missing; release cannot be verified.")
    production_url = f"https://{subdomain}"
    manifest_url = (
        f"{production_url}/release.json?"
        + urlparse.urlencode({"release": release_identity["release_id"]})
    )
    last_deployment: Optional[dict[str, Any]] = None
    last_manifest: Optional[dict[str, Any]] = None
    last_error: Optional[Exception] = None
    for attempt in range(1, 9):
        try:
            last_deployment = await latest_deployment(project_name, environment="production")
            last_manifest = await fetch_public_json(manifest_url)
            deployment_matches = cloudflare_deployment_matches(last_deployment, release_identity)
            manifest_matches = (
                last_manifest.get("release_id") == release_identity["release_id"]
                and last_manifest.get("deployment_branch") == release_identity["deployment_branch"]
                and last_manifest.get("source_commit") == release_identity["source_commit"]
            )
            if deployment_matches and manifest_matches:
                return {
                    "verified": True,
                    "production_url": production_url,
                    "manifest_url": manifest_url,
                    "deployment": last_deployment,
                    "release": last_manifest,
                }
        except (RuntimeError, urlerror.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt < 8:
            await asyncio.sleep(min(8, attempt))
    details = {
        "expected": release_identity,
        "latest_production_deployment": last_deployment,
        "production_manifest": last_manifest,
        "last_error": str(last_error) if last_error else None,
    }
    raise RuntimeError(
        "Cloudflare command completed, but the production release could not be verified: "
        + json.dumps(details, ensure_ascii=False)
    )
