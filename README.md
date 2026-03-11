# Ansible GUI

A simple localhost web app for running Ansible playbooks without touching the command line. Point it at your Ansible repo, open a browser, pick your targets, and hit Run. Built for IT admins who know their infrastructure but don't live in a terminal.

## Stack

- **Backend**: FastAPI + uvicorn
- **Frontend**: Single HTML file, vanilla JS, no build step
- **Streaming**: Server-Sent Events (SSE) for live playbook output

## Requirements

- Python 3.10+
- Ansible installed and available on `PATH` (`ansible-playbook` and `ansible-inventory`)
- WSL Ubuntu (or any Linux — native or VM)

## Installation

```bash
# 1. Clone
git clone https://github.com/bknefelwightco/ansible-gui.git
cd ansible-gui

# 2. Create a virtualenv and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

All settings are via environment variables — no config files to edit.

| Variable      | Default         | Description                                          |
|---------------|-----------------|------------------------------------------------------|
| `ANSIBLE_DIR` | `~/ansible`     | Path to your Ansible repo root (required in practice) |
| `PLAYBOOK`    | `site.yml`      | Playbook filename, relative to `ANSIBLE_DIR`         |
| `INVENTORY`   | `inventory.yml` | Inventory filename, relative to `ANSIBLE_DIR`        |

## Running

```bash
export ANSIBLE_DIR=/path/to/your/ansible/repo
./run.sh
```

Then open **http://localhost:8080** in your browser.

`run.sh` auto-activates `.venv` or `venv` if present; falls back to system Python. Override any variable inline:

```bash
ANSIBLE_DIR=/opt/infra PLAYBOOK=deploy.yml INVENTORY=hosts.yml ./run.sh
```

## Usage

1. **Select hosts** — The left sidebar loads your inventory grouped by Ansible group. Check one or more hosts (or use **All**).
2. **Select tags** *(optional)* — Check tags to limit which roles/tasks run. Leave all unchecked to run the full playbook.
3. **Vault password** *(if needed)* — Enter your Ansible Vault password. It's written to a `chmod 600` temp file, passed to `--vault-password-file`, and deleted immediately after the run. It never touches CLI args or disk beyond the temp file.
4. **Check mode** — Toggle on to do a dry run (see below).
5. **Run Playbook** — Hit the button. A confirmation prompt shows the target hosts. Confirm and watch output stream live in the console on the right.

Output is color-coded: green for `ok`/`changed`, red for `FAILED`/`ERROR`, bold blue for the PLAY RECAP.

You can **Download** the console output as a `.txt` file at any time.

## Abort

While a playbook is running, the **Run** button becomes **Abort Run**. Clicking it sends a `POST /api/abort` to kill the server-side `ansible-playbook` process immediately. The SSE stream closes and the console logs the abort.

## Check Mode

Enabling **Check mode** appends `--check` to the `ansible-playbook` command. Ansible simulates all tasks without making any actual changes. Use it to preview what *would* change before committing a real run. The console will note `🔍 Check mode ON — no changes will be applied` at the top of each run.

## Inventory Editor

Click the **📋 Inventory Editor** button in the header to open the inventory editor panel. This lets you view and edit host variables directly in the browser — no YAML editing required.

### What it does

- **Browse hosts by group** — Hosts are listed per Ansible group. Click a host to load its variables.
- **Edit host variables** — Fields render as smart inputs: booleans become toggles, enum fields become dropdowns, IP/string fields are plain text. Changes are saved back to the inventory YAML immediately.
- **Add hosts** — Use the **+ Add Host** button within any group. New hosts are pre-populated with schema defaults for that group.
- **Delete hosts** — Remove a host from inventory with the delete button. A confirmation step prevents accidents.
- **YAML comment preservation** — Edits use `ruamel.yaml` under the hood, so any comments in your inventory file survive round-trips through the editor.
- **Automatic backup** — Before every write, the backend saves a `.inventory.yml.bak` alongside the live file.

### Group schemas

The editor ships with a built-in variable schema for known groups (e.g. `Windows`, `Imaged`). This drives the smart input types and default values when adding new hosts. Groups without a schema still work — variables show as plain text fields.

## Project Structure

```
ansible-gui/
├── app.py              # FastAPI backend — API endpoints, SSE streaming, inventory CRUD
├── config.py           # Config — reads ANSIBLE_DIR, PLAYBOOK, INVENTORY from env
├── requirements.txt    # Python deps (fastapi, uvicorn, ruamel.yaml)
├── run.sh              # Startup script — activates venv, launches uvicorn
├── static/
│   └── index.html      # Single-page frontend — all UI, vanilla JS
└── README.md
```

## Security Notes

- Localhost only (`127.0.0.1:8080`) — not exposed to the network by default
- No authentication — rely on host-level access control
- Vault passwords are written to a `chmod 600` temp file and deleted after each run
- Host and tag inputs are validated against an allowlist pattern before being passed to the shell
- Inventory writes are protected by a server-side async lock to prevent concurrent edits
