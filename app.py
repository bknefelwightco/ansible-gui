"""
app.py — Ansible GUI backend
FastAPI app that wraps ansible-inventory and ansible-playbook
for non-savvy IT admins. Streams output via SSE.
"""

import asyncio
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config

app = FastAPI(title="Ansible GUI")

# ---------------------------------------------------------------------------
# In-memory last-run state
# ---------------------------------------------------------------------------
_last_run: dict = {
    "returncode": None,
    "timestamp": None,
    "success": None,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ansible_dir() -> Path:
    d = Path(config.ANSIBLE_DIR).expanduser().resolve()
    return d


def _playbook_path() -> Path:
    return _ansible_dir() / config.PLAYBOOK


def _inventory_path() -> Path:
    return _ansible_dir() / config.INVENTORY


async def _run_command(cmd: list[str], cwd: str) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail=f"Command not found: {cmd[0]}. Is Ansible installed?",
        )


# ---------------------------------------------------------------------------
# API: inventory
# ---------------------------------------------------------------------------

@app.get("/api/inventory")
async def get_inventory():
    """
    Run `ansible-inventory --list --json` and return structured hosts + groups.
    """
    ansible_dir = _ansible_dir()
    inventory = _inventory_path()

    cmd = [
        "ansible-inventory",
        "-i", str(inventory),
        "--list",
        "--export",
    ]

    returncode, stdout, stderr = await _run_command(cmd, str(ansible_dir))

    if returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"ansible-inventory failed:\n{stderr or stdout}",
        )

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse inventory JSON: {e}")

    # Build groups → hosts mapping
    # The JSON has a top-level "_meta" key with "hostvars" and group keys.
    meta = data.get("_meta", {})
    hostvars = meta.get("hostvars", {})
    all_hosts = set(hostvars.keys())

    groups: dict[str, list[str]] = {}
    for group_name, group_data in data.items():
        if group_name == "_meta":
            continue
        if not isinstance(group_data, dict):
            continue
        hosts_in_group = group_data.get("hosts", [])
        if hosts_in_group:
            groups[group_name] = sorted(hosts_in_group)

    # If a host appears in no group, put it under "ungrouped"
    grouped_hosts: set[str] = set()
    for members in groups.values():
        grouped_hosts.update(members)
    ungrouped = sorted(all_hosts - grouped_hosts)
    if ungrouped:
        groups["ungrouped"] = ungrouped

    return {
        "hosts": sorted(all_hosts),
        "groups": groups,
    }


# ---------------------------------------------------------------------------
# API: tags
# ---------------------------------------------------------------------------

@app.get("/api/tags")
async def get_tags():
    """
    Run `ansible-playbook site.yml --list-tags` and parse TASK TAGS lines.
    """
    ansible_dir = _ansible_dir()
    inventory = _inventory_path()
    playbook = _playbook_path()

    cmd = [
        "ansible-playbook",
        str(playbook),
        "-i", str(inventory),
        "--list-tags",
    ]

    returncode, stdout, stderr = await _run_command(cmd, str(ansible_dir))

    if returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"ansible-playbook --list-tags failed:\n{stderr or stdout}",
        )

    # Parse lines like: "      TASK TAGS: [tag1, tag2, tag3]"
    tag_set: set[str] = set()
    for line in (stdout + stderr).splitlines():
        match = re.search(r"TASK TAGS:\s*\[([^\]]*)\]", line)
        if match:
            raw = match.group(1)
            for tag in raw.split(","):
                tag = tag.strip()
                if tag:
                    tag_set.add(tag)

    return {"tags": sorted(tag_set)}


# ---------------------------------------------------------------------------
# API: run (SSE streaming)
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    hosts: list[str]
    tags: list[str] = []
    vault_password: str = ""


@app.post("/api/run")
async def run_playbook(req: RunRequest):
    """
    Stream ansible-playbook output as Server-Sent Events.
    Vault password is written to a chmod-600 temp file and cleaned up after.
    """
    if not req.hosts:
        raise HTTPException(status_code=400, detail="At least one host must be selected.")

    ansible_dir = _ansible_dir()
    inventory = _inventory_path()
    playbook = _playbook_path()

    async def event_stream():
        vault_file_path: Optional[str] = None
        proc = None

        try:
            # Write vault password to a secure temp file
            if req.vault_password:
                tmp = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".tmp", delete=False
                )
                tmp.write(req.vault_password)
                tmp.flush()
                tmp.close()
                vault_file_path = tmp.name
                os.chmod(vault_file_path, stat.S_IRUSR | stat.S_IWUSR)  # 600

            # Build command
            cmd = [
                "ansible-playbook",
                str(playbook),
                "-i", str(inventory),
                "--limit", ",".join(req.hosts),
            ]
            if req.tags:
                cmd += ["--tags", ",".join(req.tags)]
            if vault_file_path:
                cmd += ["--vault-password-file", vault_file_path]

            yield f"data: 🚀 Starting playbook: {config.PLAYBOOK}\n\n"
            yield f"data: 📂 Working dir: {ansible_dir}\n\n"
            yield f"data: 🎯 Targets: {', '.join(req.hosts)}\n\n"
            if req.tags:
                yield f"data: 🏷️  Tags: {', '.join(req.tags)}\n\n"
            yield "data: \n\n"

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(ansible_dir),
                env={**os.environ, "ANSIBLE_FORCE_COLOR": "0", "PYTHONUNBUFFERED": "1"},
            )

            # Stream output line by line
            async for raw_line in proc.stdout:
                line = raw_line.decode(errors="replace").rstrip()
                # Escape SSE-sensitive characters (data lines can't contain raw newlines)
                yield f"data: {line}\n\n"

            await proc.wait()
            returncode = proc.returncode

        except asyncio.CancelledError:
            # Client disconnected — kill the subprocess
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.sleep(1)
                    if proc.returncode is None:
                        proc.kill()
                except ProcessLookupError:
                    pass
            returncode = -1
            raise

        except FileNotFoundError:
            yield "data: ❌ Error: ansible-playbook not found. Is Ansible installed?\n\n"
            yield "event: done\ndata: 1\n\n"
            return

        except Exception as e:
            yield f"data: ❌ Unexpected error: {e}\n\n"
            yield "event: done\ndata: 1\n\n"
            return

        finally:
            # Clean up vault temp file
            if vault_file_path and os.path.exists(vault_file_path):
                try:
                    os.unlink(vault_file_path)
                except OSError:
                    pass

        # Update in-memory last-run state
        _last_run["returncode"] = returncode
        _last_run["timestamp"] = datetime.now(timezone.utc).isoformat()
        _last_run["success"] = returncode == 0

        status_icon = "✅" if returncode == 0 else "❌"
        yield f"data: \n\n"
        yield f"data: {status_icon} Playbook finished with return code {returncode}\n\n"
        yield f"event: done\ndata: {returncode}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# API: status
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def get_status():
    """Return the last run result."""
    return _last_run


# ---------------------------------------------------------------------------
# Static files + root
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    index = Path("static/index.html")
    if index.exists():
        return HTMLResponse(content=index.read_text())
    return HTMLResponse("<h1>Ansible GUI</h1><p>static/index.html not found.</p>")


# ---------------------------------------------------------------------------
# Entry point (for direct `python app.py` use)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8080, reload=False)
