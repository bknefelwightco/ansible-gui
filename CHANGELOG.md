# Changelog

All notable changes to this project will be documented in this file.

---

## [Unreleased] — 2026-03-11

### Added

- **Inventory Editor** — New in-browser panel for editing `inventory.yml` without touching the file directly. Access it via the **📋 Inventory Editor** button in the header.
  - Browse hosts organized by Ansible group
  - Edit host variables with smart input types: booleans render as toggles, enum fields as dropdowns, IP/string fields as text inputs
  - Add new hosts with schema-driven defaults per group
  - Delete hosts with a confirmation step
  - Group schemas defined for `Windows` and `Imaged` groups (`ansible_host`, `host_depart`, `host_gpu`, `host_type`, `remote_desktop_user`, `project_install`, `new_hostname`, `domain_ou`)
- **YAML comment preservation** — Inventory writes now use `ruamel.yaml`, preserving any hand-written comments in the inventory file through editor round-trips
- **Automatic inventory backup** — A `.inventory.yml.bak` file is created before every inventory write
- **Concurrency lock** — Server-side `asyncio.Lock` prevents simultaneous inventory writes from corrupting the file
- **`ruamel.yaml` dependency** — Added to `requirements.txt` (`ruamel.yaml>=0.18.0`)

### Fixed

- **Inventory editor — round 3** (`e7e1b5e`):
  - Fixed test contract for inventory CRUD endpoints
  - Removed leftover toast notifications from the editor UI
  - Added dirty-check guard to the Add Host flow to prevent unsaved-changes loss

- **Inventory editor — round 2** (`5df1ad4`):
  - Fixed concurrency issue where parallel requests could interleave inventory writes
  - Accessibility improvements: proper ARIA labels and keyboard navigation for editor controls
  - UX polish: loading states, error feedback, and panel open/close behavior

---

## Prior to changelog

Earlier changes tracked via git log only. See `git log --oneline` for full history.
