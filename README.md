# Ansible GUI

A simple localhost web app for running Ansible playbooks via browser.
Built for non-Ansible-savvy IT admins — point it at your Ansible repo and go.

## Stack

- **Backend**: FastAPI + uvicorn
- **Frontend**: Single HTML file, vanilla JS, no build step
- **Streaming**: Server-Sent Events (SSE) for live playbook output

## Quick Start

```bash
# 1. Clone
git clone https://github.com/bknefelwightco/ansible-gui.git
cd ansible-gui

# 2. Create virtualenv & install deps
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Point it at your Ansible repo
export ANSIBLE_DIR=/path/to/your/ansible/repo

# 4. Run
./run.sh
# → http://127.0.0.1:8080
```

## Configuration

All settings via environment variables (no config file editing needed):

| Variable      | Default        | Description                              |
|---------------|---------------|------------------------------------------|
| `ANSIBLE_DIR` | `~/ansible`   | Path to your Ansible repo root           |
| `PLAYBOOK`    | `site.yml`    | Playbook filename (relative to ANSIBLE_DIR) |
| `INVENTORY`   | `inventory.yml` | Inventory filename (relative to ANSIBLE_DIR) |

Example:
```bash
ANSIBLE_DIR=/opt/infra PLAYBOOK=deploy.yml INVENTORY=hosts.yml ./run.sh
```

## Features

- **Inventory browser**: Loads hosts grouped by Ansible group (via `ansible-inventory --list`)
- **Tag selector**: Loads available tags (via `ansible-playbook --list-tags`)
- **Vault support**: Password entered in-browser, written to a secure temp file (chmod 600), never exposed on CLI args
- **Live output**: Playbook stdout streams in real-time via SSE to a terminal-style console
- **Last run status**: Shown in footer — return code + timestamp

## Requirements

- Python 3.10+
- Ansible (`ansible-inventory` + `ansible-playbook` must be in PATH)
- The user running the server needs access to the Ansible repo and any SSH keys

## Security Notes

- Localhost only (`127.0.0.1:8080`) — not exposed to the network
- No authentication (rely on host-level access control)
- Vault passwords are written to a `chmod 600` temp file and deleted immediately after each run
- No secrets are stored on disk between runs

## Project Layout

```
ansible-gui/
├── app.py              # FastAPI backend
├── config.py           # Config (reads env vars)
├── requirements.txt    # fastapi uvicorn
├── static/
│   └── index.html      # Single-page frontend
├── run.sh              # ./run.sh to start
└── README.md
```
