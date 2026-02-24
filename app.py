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
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config

# ---------------------------------------------------------------------------
# In-memory last-run state
# ---------------------------------------------------------------------------
_last_run: dict = {
    "returncode": None,
    "timestamp": None,
    "success": None,
}

# Active process reference for abort endpoint
_active_proc: Optional[asyncio.subprocess.Process] = None

# ---------------------------------------------------------------------------
# Input validation patterns
# ---------------------------------------------------------------------------
_SAFE_HOST = re.compile(r'^[\w.\-:@]+$')
_SAFE_TAG  = re.compile(r'^[\w.\-]+$')


def _validate_hosts(hosts: list[str]) -> None:
    for h in hosts:
        if not _SAFE_HOST.match(h):
            raise HTTPException(400, f"Invalid host name: {h!r}")


def _validate_tags(tags: list[str]) -> None:
    for t in tags:
        if not _SAFE_TAG.match(t):
            raise HTTPException(400, f"Invalid tag: {t!r}")


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
        # Raise RuntimeError; callers decide how to surface it
        raise RuntimeError(f"Command not found: {cmd[0]}. Is Ansible installed?")


# ---------------------------------------------------------------------------
# Startup lifespan validation
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app):
    d = _ansible_dir()
    if not d.is_dir():
        raise RuntimeError(f"ANSIBLE_DIR does not exist: {d}")
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Ansible GUI", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Static files — path relative to app.py, not CWD
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ---------------------------------------------------------------------------
# Inventory children resolver
# ---------------------------------------------------------------------------

def _resolve_group_hosts(group_name: str, data: dict, visited: set) -> set:
    if group_name in visited:
        return set()
    visited.add(group_name)
    gd = data.get(group_name, {})
    if not isinstance(gd, dict):
        return set()
    hosts = set(gd.get("hosts", []))
    for child in gd.get("children", []):
        hosts |= _resolve_group_hosts(child, data, visited)
    return hosts


# ---------------------------------------------------------------------------
# API: inventory
# ---------------------------------------------------------------------------

@app.get("/api/inventory")
async def get_inventory():
    """
    Run `ansible-inventory --list --export` and return structured hosts + groups.

    Returns a dict with:
      - hosts: sorted list of all host names
      - groups: dict mapping group name → sorted list of member hosts
                (group membership is resolved transitively through children)
    """
    ansible_dir = _ansible_dir()
    inventory = _inventory_path()

    cmd = [
        "ansible-inventory",
        "-i", str(inventory),
        "--list",
        "--export",
    ]

    try:
        returncode, stdout, stderr = await _run_command(cmd, str(ansible_dir))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    if returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"ansible-inventory failed:\n{stderr or stdout}",
        )

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse inventory JSON: {e}")

    # Build groups → hosts mapping (with transitive children resolution)
    meta = data.get("_meta", {})
    hostvars = meta.get("hostvars", {})
    all_hosts = set(hostvars.keys())

    groups: dict[str, list[str]] = {}
    for group_name, group_data in data.items():
        if group_name == "_meta":
            continue
        if not isinstance(group_data, dict):
            continue
        resolved = _resolve_group_hosts(group_name, data, set())
        if resolved:
            groups[group_name] = sorted(resolved)

    # If a host appears in no explicit group, put it under "ungrouped"
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
    Run `ansible-playbook <PLAYBOOK> --list-tags` and parse TASK TAGS lines.

    Parses all lines matching `TASK TAGS: [tag1, tag2, ...]` from combined
    stdout+stderr and returns a deduplicated, sorted list.
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

    try:
        returncode, stdout, stderr = await _run_command(cmd, str(ansible_dir))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

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
    check_mode: bool = False


@app.post("/api/run")
async def run_playbook(req: RunRequest):
    """
    Stream ansible-playbook output as Server-Sent Events (SSE).

    Validates host and tag inputs against safe-character allowlists before
    building the command. Vault password (if provided) is written to a
    chmod-600 temp file, passed via --vault-password-file, and deleted
    immediately after the run. Appends --check when check_mode is True.

    Each output line is sent as a `data:` SSE event. A final `event: done`
    event carries the playbook return code. If the client disconnects,
    the subprocess is killed via CancelledError handling.
    """
    global _active_proc

    if not req.hosts:
        raise HTTPException(status_code=400, detail="At least one host must be selected.")

    # Validate host and tag inputs before building the command
    _validate_hosts(req.hosts)
    _validate_tags(req.tags)

    ansible_dir = _ansible_dir()
    inventory = _inventory_path()
    playbook = _playbook_path()

    async def event_stream():
        global _active_proc
        vault_file_path: Optional[str] = None
        proc = None
        returncode = None  # initialised here to avoid UnboundLocalError

        try:
            # Write vault password to a secure temp file — chmod BEFORE any write
            if req.vault_password:
                fd, vault_file_path = tempfile.mkstemp(suffix=".vaultpw")
                try:
                    os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)  # 600 before any write
                    with os.fdopen(fd, "w") as f:
                        f.write(req.vault_password)
                        f.write("\n")  # ansible expects trailing newline
                except Exception:
                    os.unlink(vault_file_path)
                    vault_file_path = None
                    raise

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
            if req.check_mode:
                cmd += ["--check"]

            yield f"data: 🚀 Starting playbook: {config.PLAYBOOK}\n\n"
            yield f"data: 📂 Working dir: {ansible_dir}\n\n"
            yield f"data: 🎯 Targets: {', '.join(req.hosts)}\n\n"
            if req.tags:
                yield f"data: 🏷️  Tags: {', '.join(req.tags)}\n\n"
            if req.check_mode:
                yield "data: 🔍 Check mode ON — no changes will be applied\n\n"
            yield ": \n\n"  # SSE comment separator — no spurious events

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(ansible_dir),
                env={**os.environ, "ANSIBLE_FORCE_COLOR": "0", "PYTHONUNBUFFERED": "1"},
            )
            _active_proc = proc

            # Stream output line by line
            async for raw_line in proc.stdout:
                line = raw_line.decode(errors="replace").rstrip()
                if not line:
                    yield ": \n\n"  # blank comment — no spurious events
                else:
                    yield f"data: {line}\n\n"

            await proc.wait()
            returncode = proc.returncode

        except asyncio.CancelledError:
            # Client disconnected — kill the subprocess immediately
            # (asyncio.sleep would re-raise CancelledError before proc.kill)
            if proc and proc.returncode is None:
                try:
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
            # Clear active proc reference
            _active_proc = None

            # Clean up vault temp file
            if vault_file_path and os.path.exists(vault_file_path):
                try:
                    os.unlink(vault_file_path)
                except OSError:
                    pass

            # Update in-memory last-run state atomically (only if we have a result)
            if returncode is not None:
                _last_run.update({
                    "returncode": returncode,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "success": returncode == 0,
                })

        status_icon = "✅" if returncode == 0 else "❌"
        yield ": \n\n"
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
# API: abort
# ---------------------------------------------------------------------------

@app.post("/api/abort")
async def abort_run():
    """Kill the currently running ansible-playbook process, if any."""
    global _active_proc
    proc = _active_proc
    if proc is None or proc.returncode is not None:
        return {"status": "no active run"}
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    return {"status": "killed"}


# ---------------------------------------------------------------------------
# API: status
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def get_status():
    """Return the last run result."""
    return _last_run


# ---------------------------------------------------------------------------
# Root route — path relative to app.py, not CWD
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the single-page frontend (static/index.html)."""
    index = BASE_DIR / "static" / "index.html"
    if index.exists():
        return HTMLResponse(content=index.read_text())
    return HTMLResponse("<h1>Ansible GUI</h1><p>static/index.html not found.</p>")


# ---------------------------------------------------------------------------
# Entry point (for direct `python app.py` use)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8080, reload=False)
