"""
config.py — Ansible GUI configuration
All settings can be overridden via environment variables.
"""

import os
from pathlib import Path

# Path to the Ansible repository (where site.yml and inventory.yml live)
ANSIBLE_DIR: str = os.environ.get("ANSIBLE_DIR", str(Path.home() / "ansible"))

# Playbook filename (relative to ANSIBLE_DIR)
PLAYBOOK: str = os.environ.get("PLAYBOOK", "site.yml")

# Inventory filename (relative to ANSIBLE_DIR)
INVENTORY: str = os.environ.get("INVENTORY", "inventory.yml")
