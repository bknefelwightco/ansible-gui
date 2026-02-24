#!/usr/bin/env bash
# run.sh — Start the Ansible GUI server
# Usage: ./run.sh
# Override settings via environment variables:
#   ANSIBLE_DIR=/path/to/your/ansible/repo ./run.sh
#   PLAYBOOK=site.yml INVENTORY=inventory.yml ./run.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtualenv if present
if [ -f ".venv/bin/activate" ]; then
  echo "[run.sh] Activating .venv"
  source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
  echo "[run.sh] Activating venv"
  source venv/bin/activate
else
  echo "[run.sh] No virtualenv found — using system Python"
fi

echo "[run.sh] Starting Ansible GUI on http://127.0.0.1:8080"
echo "[run.sh] ANSIBLE_DIR=${ANSIBLE_DIR:-~/ansible (default)}"

exec uvicorn app:app --host 127.0.0.1 --port 8080 --log-level info
