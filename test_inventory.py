"""
test_inventory.py — Anvil 🔨 test suite for inventory host variable editor
Commit 6f65eff: feat: add inventory host variable editor
"""

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

import httpx
import pytest
from ruamel.yaml import YAML

# ---------------------------------------------------------------------------
# Fixture inventory YAML — comments included to test preservation
# ---------------------------------------------------------------------------

FIXTURE_INVENTORY_YAML = """\
all:
  children:
    Windows:
      vars:
        # Secret: do NOT expose this in API responses
        ansible_password: "{{ lookup('bitwarden', 'windows_admin_pass') }}"
        ansible_user: administrator
        ansible_connection: winrm
      hosts:
        DESKTOP-ABC:
          ansible_host: 192.168.1.100  # primary workstation
          host_depart: acct
          host_gpu: false
          host_type: desktop
          remote_desktop_user: jsmith
          project_install: false
        LAPTOP-XYZ:
          ansible_host: 192.168.1.101
          host_depart: mktg
          host_gpu: false
          host_type: laptop
          remote_desktop_user: none
          project_install: true
    Imaged:
      hosts:
        NEWHOST-001:
          ansible_host: 192.168.2.50
          new_hostname: newhost001
          domain_ou: Field
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ansible_dir(tmp_path, monkeypatch):
    """Create a temp ANSIBLE_DIR with fixture inventory and patch config."""
    inv_path = tmp_path / "inventory.yml"
    inv_path.write_text(FIXTURE_INVENTORY_YAML)

    # Also create a dummy site.yml so lifespan doesn't fail
    (tmp_path / "site.yml").write_text("---\n- hosts: all\n  tasks: []\n")

    # Patch config before app functions resolve paths
    import config
    monkeypatch.setattr(config, "ANSIBLE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "INVENTORY", "inventory.yml")
    monkeypatch.setattr(config, "PLAYBOOK", "site.yml")

    return tmp_path


@pytest.fixture()
def client(ansible_dir):
    """Synchronous TestClient backed by the FastAPI app."""
    # Import app after config is patched
    from fastapi.testclient import TestClient
    import app as app_module

    with TestClient(app_module.app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def inv_path(ansible_dir):
    return ansible_dir / "inventory.yml"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def read_yaml(path: Path) -> dict:
    yaml = YAML()
    yaml.preserve_quotes = True
    with open(path) as f:
        return yaml.load(f)


# ---------------------------------------------------------------------------
# Tests: GET /api/inventory/raw
# ---------------------------------------------------------------------------


class TestGetInventoryRaw:
    def test_returns_groups_key(self, client):
        r = client.get("/api/inventory/raw")
        assert r.status_code == 200
        body = r.json()
        assert "groups" in body

    def test_returns_schemas_key(self, client):
        r = client.get("/api/inventory/raw")
        assert r.status_code == 200
        body = r.json()
        assert "schemas" in body
        assert "Windows" in body["schemas"]

    def test_windows_group_has_hosts(self, client):
        r = client.get("/api/inventory/raw")
        body = r.json()
        groups = body["groups"]
        assert "Windows" in groups
        assert "hosts" in groups["Windows"]
        assert "DESKTOP-ABC" in groups["Windows"]["hosts"]
        assert "LAPTOP-XYZ" in groups["Windows"]["hosts"]

    def test_host_vars_present(self, client):
        r = client.get("/api/inventory/raw")
        body = r.json()
        host = body["groups"]["Windows"]["hosts"]["DESKTOP-ABC"]
        assert host["ansible_host"] == "192.168.1.100"
        assert host["host_depart"] == "acct"
        assert host["host_type"] == "desktop"

    def test_group_vars_secrets_not_returned(self, client):
        """Group-level vars (especially secret lookups) must NOT appear in host data."""
        r = client.get("/api/inventory/raw")
        body = r.json()
        # Check no host under Windows contains group-level secrets
        for hostname, hvars in body["groups"]["Windows"]["hosts"].items():
            assert "ansible_password" not in hvars, (
                f"Secret ansible_password leaked into host {hostname!r}"
            )
            assert "ansible_user" not in hvars, (
                f"Group var ansible_user leaked into host {hostname!r}"
            )
            assert "ansible_connection" not in hvars, (
                f"Group var ansible_connection leaked into host {hostname!r}"
            )

    def test_imaged_group_present(self, client):
        r = client.get("/api/inventory/raw")
        body = r.json()
        assert "Imaged" in body["groups"]
        assert "NEWHOST-001" in body["groups"]["Imaged"]["hosts"]

    def test_structure_matches_schema(self, client):
        """Every group in groups must have a 'hosts' dict."""
        r = client.get("/api/inventory/raw")
        body = r.json()
        for group_name, group_data in body["groups"].items():
            assert isinstance(group_data, dict), f"{group_name} is not a dict"
            assert "hosts" in group_data, f"{group_name} missing 'hosts'"
            assert isinstance(group_data["hosts"], dict), f"{group_name}.hosts is not a dict"


# ---------------------------------------------------------------------------
# Tests: PUT /api/inventory/host/{group}/{hostname}
# ---------------------------------------------------------------------------


class TestUpdateHost:
    def test_update_existing_host(self, client, inv_path):
        payload = {
            "ansible_host": "192.168.1.200",
            "host_depart": "corp",
            "host_gpu": False,
            "host_type": "desktop",
            "remote_desktop_user": "jdoe",
            "project_install": True,
        }
        r = client.put("/api/inventory/host/Windows/DESKTOP-ABC", json=payload)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["hostname"] == "DESKTOP-ABC"

    def test_update_persisted_to_yaml(self, client, inv_path):
        payload = {
            "ansible_host": "192.168.1.200",
            "host_depart": "corp",
            "host_gpu": False,
            "host_type": "desktop",
            "remote_desktop_user": "jdoe",
            "project_install": True,
        }
        client.put("/api/inventory/host/Windows/DESKTOP-ABC", json=payload)
        data = read_yaml(inv_path)
        host = data["all"]["children"]["Windows"]["hosts"]["DESKTOP-ABC"]
        assert str(host["ansible_host"]) == "192.168.1.200"
        assert str(host["host_depart"]) == "corp"

    def test_update_creates_backup(self, client, inv_path):
        payload = {
            "ansible_host": "192.168.1.200",
            "host_depart": "corp",
            "host_gpu": False,
            "host_type": "desktop",
            "remote_desktop_user": "jdoe",
            "project_install": False,
        }
        client.put("/api/inventory/host/Windows/DESKTOP-ABC", json=payload)
        bak_path = inv_path.parent / f".{inv_path.name}.bak"
        assert bak_path.exists(), "Backup file was not created"

    def test_update_backup_contains_original(self, client, inv_path):
        """Backup should contain the original content before the write."""
        original_content = inv_path.read_text()
        payload = {
            "ansible_host": "10.0.0.1",
            "host_depart": "corp",
            "host_gpu": True,
            "host_type": "laptop",
            "remote_desktop_user": "none",
            "project_install": False,
        }
        client.put("/api/inventory/host/Windows/DESKTOP-ABC", json=payload)
        bak_path = inv_path.parent / f".{inv_path.name}.bak"
        assert bak_path.read_text() == original_content

    def test_update_nonexistent_host_404(self, client):
        r = client.put(
            "/api/inventory/host/Windows/DOES-NOT-EXIST",
            json={"ansible_host": "1.2.3.4"},
        )
        assert r.status_code == 404

    def test_update_nonexistent_group_404(self, client):
        r = client.put(
            "/api/inventory/host/NoSuchGroup/DESKTOP-ABC",
            json={"ansible_host": "1.2.3.4"},
        )
        assert r.status_code == 404

    def test_comments_preserved_after_update(self, client, inv_path):
        """ruamel.yaml should preserve inline YAML comments on write-back."""
        payload = {
            "ansible_host": "192.168.1.199",
            "host_depart": "acct",
            "host_gpu": False,
            "host_type": "desktop",
            "remote_desktop_user": "jsmith",
            "project_install": False,
        }
        client.put("/api/inventory/host/Windows/DESKTOP-ABC", json=payload)
        written = inv_path.read_text()
        # The comment on DESKTOP-ABC's ansible_host line should still be there
        assert "primary workstation" in written, (
            "Inline comment 'primary workstation' was lost after write-back"
        )


# ---------------------------------------------------------------------------
# Tests: POST /api/inventory/host/{group}
# ---------------------------------------------------------------------------


class TestAddHost:
    def test_add_host_to_windows(self, client, inv_path):
        payload = {
            "hostname": "NEWDESKTOP-001",
            "vars": {
                "ansible_host": "192.168.1.150",
                "host_depart": "arch",
                "host_gpu": False,
                "host_type": "desktop",
                "remote_desktop_user": "barchitect",
                "project_install": True,
            },
        }
        r = client.post("/api/inventory/host/Windows", json=payload)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["hostname"] == "NEWDESKTOP-001"

    def test_add_host_persisted(self, client, inv_path):
        payload = {
            "hostname": "NEWDESKTOP-001",
            "vars": {"ansible_host": "192.168.1.150", "host_depart": "arch"},
        }
        client.post("/api/inventory/host/Windows", json=payload)
        data = read_yaml(inv_path)
        hosts = data["all"]["children"]["Windows"]["hosts"]
        assert "NEWDESKTOP-001" in hosts

    def test_add_host_appears_in_raw(self, client):
        payload = {
            "hostname": "NEWDESKTOP-002",
            "vars": {"ansible_host": "192.168.1.151", "host_depart": "corp"},
        }
        client.post("/api/inventory/host/Windows", json=payload)
        r = client.get("/api/inventory/raw")
        body = r.json()
        assert "NEWDESKTOP-002" in body["groups"]["Windows"]["hosts"]

    def test_add_duplicate_host_409(self, client):
        """Adding an already-existing host should return 409 Conflict."""
        payload = {"hostname": "DESKTOP-ABC", "vars": {}}
        r = client.post("/api/inventory/host/Windows", json=payload)
        assert r.status_code == 409

    def test_add_host_to_nonexistent_group_404(self, client):
        payload = {"hostname": "NEWHOST", "vars": {}}
        r = client.post("/api/inventory/host/NoSuchGroup", json=payload)
        assert r.status_code == 404

    def test_add_creates_backup(self, client, inv_path):
        payload = {"hostname": "BKPTEST-001", "vars": {"ansible_host": "10.0.1.1"}}
        client.post("/api/inventory/host/Windows", json=payload)
        bak_path = inv_path.parent / f".{inv_path.name}.bak"
        assert bak_path.exists()


# ---------------------------------------------------------------------------
# Tests: DELETE /api/inventory/host/{group}/{hostname}
# ---------------------------------------------------------------------------


class TestDeleteHost:
    def test_delete_existing_host(self, client):
        r = client.delete("/api/inventory/host/Windows/LAPTOP-XYZ")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["hostname"] == "LAPTOP-XYZ"

    def test_delete_removes_from_yaml(self, client, inv_path):
        client.delete("/api/inventory/host/Windows/LAPTOP-XYZ")
        data = read_yaml(inv_path)
        hosts = data["all"]["children"]["Windows"]["hosts"]
        assert "LAPTOP-XYZ" not in hosts

    def test_delete_not_in_raw_after(self, client):
        client.delete("/api/inventory/host/Windows/LAPTOP-XYZ")
        r = client.get("/api/inventory/raw")
        body = r.json()
        assert "LAPTOP-XYZ" not in body["groups"]["Windows"]["hosts"]

    def test_delete_nonexistent_host_404(self, client):
        r = client.delete("/api/inventory/host/Windows/DOES-NOT-EXIST")
        assert r.status_code == 404

    def test_delete_nonexistent_group_404(self, client):
        r = client.delete("/api/inventory/host/NoSuchGroup/DESKTOP-ABC")
        assert r.status_code == 404

    def test_delete_creates_backup(self, client, inv_path):
        client.delete("/api/inventory/host/Windows/LAPTOP-XYZ")
        bak_path = inv_path.parent / f".{inv_path.name}.bak"
        assert bak_path.exists()

    def test_other_hosts_unaffected(self, client):
        """Deleting one host must not remove others in the same group."""
        client.delete("/api/inventory/host/Windows/LAPTOP-XYZ")
        r = client.get("/api/inventory/raw")
        body = r.json()
        assert "DESKTOP-ABC" in body["groups"]["Windows"]["hosts"], (
            "DESKTOP-ABC was incorrectly removed when LAPTOP-XYZ was deleted"
        )


# ---------------------------------------------------------------------------
# Tests: Input validation
# ---------------------------------------------------------------------------


class TestInputValidation:
    # ---- Bad hostname patterns ----

    def test_bad_hostname_slash_put(self, client):
        r = client.put("/api/inventory/host/Windows/DESK%2FBAD", json={})
        # %2F decoded = /, which creates path segments — FastAPI should 404 or 400
        assert r.status_code in (400, 404, 422)

    def test_bad_hostname_special_chars_delete(self, client):
        # Semicolon in hostname — should be rejected (encoded as %3B)
        r = client.delete("/api/inventory/host/Windows/HOST%3BBAD")
        assert r.status_code in (400, 404, 422)

    def test_add_host_bad_hostname_chars(self, client):
        """Hostname with spaces should be rejected."""
        r = client.post(
            "/api/inventory/host/Windows",
            json={"hostname": "HOST WITH SPACES", "vars": {}},
        )
        assert r.status_code == 400

    def test_add_host_empty_hostname(self, client):
        r = client.post(
            "/api/inventory/host/Windows",
            json={"hostname": "", "vars": {}},
        )
        assert r.status_code == 400

    def test_add_host_hostname_with_semicolon(self, client):
        r = client.post(
            "/api/inventory/host/Windows",
            json={"hostname": "HOST;bad", "vars": {}},
        )
        assert r.status_code == 400

    # ---- Bad IP address validation ----

    def test_add_host_bad_ip(self, client):
        r = client.post(
            "/api/inventory/host/Windows",
            json={"hostname": "VALID-HOST", "vars": {"ansible_host": "999.999.999.999"}},
        )
        assert r.status_code == 400

    def test_add_host_ip_with_letters(self, client):
        r = client.post(
            "/api/inventory/host/Windows",
            json={"hostname": "VALID-HOST", "vars": {"ansible_host": "192.168.1.abc"}},
        )
        assert r.status_code == 400

    def test_add_host_ip_hostname_not_rejected(self, client):
        """A valid IP should pass validation."""
        r = client.post(
            "/api/inventory/host/Windows",
            json={"hostname": "VALID-HOST-IP",
                  "vars": {"ansible_host": "10.0.0.1", "host_depart": "corp"}},
        )
        assert r.status_code == 200

    def test_put_bad_ip_rejected(self, client):
        r = client.put(
            "/api/inventory/host/Windows/DESKTOP-ABC",
            json={"ansible_host": "not-an-ip", "host_depart": "acct"},
        )
        assert r.status_code == 400

    def test_put_valid_ip_accepted(self, client):
        r = client.put(
            "/api/inventory/host/Windows/DESKTOP-ABC",
            json={
                "ansible_host": "10.20.30.40",
                "host_depart": "acct",
                "host_gpu": False,
                "host_type": "desktop",
                "remote_desktop_user": "jsmith",
                "project_install": False,
            },
        )
        assert r.status_code == 200

    def test_add_host_empty_ip_not_rejected(self, client):
        """Empty ansible_host should be allowed (field may be filled in later)."""
        r = client.post(
            "/api/inventory/host/Windows",
            json={"hostname": "NOIP-HOST", "vars": {"ansible_host": "", "host_depart": "corp"}},
        )
        # Empty string — IP check only runs when value is non-empty
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Tests: Comment preservation (ruamel.yaml write-back)
# ---------------------------------------------------------------------------


class TestCommentPreservation:
    def test_inline_comment_on_host_survived_add(self, client, inv_path):
        """Adding a new host should not destroy existing inline comments."""
        payload = {
            "hostname": "COMMENT-TEST",
            "vars": {"ansible_host": "10.10.10.10"},
        }
        client.post("/api/inventory/host/Windows", json=payload)
        written = inv_path.read_text()
        assert "primary workstation" in written

    def test_inline_comment_survived_delete(self, client, inv_path):
        """Deleting a different host should leave existing comments intact."""
        client.delete("/api/inventory/host/Windows/LAPTOP-XYZ")
        written = inv_path.read_text()
        assert "primary workstation" in written

    def test_yaml_parses_correctly_after_roundtrip(self, client, inv_path):
        """After a PUT, the written YAML must be parseable and structurally valid."""
        payload = {
            "ansible_host": "192.168.1.100",
            "host_depart": "acct",
            "host_gpu": False,
            "host_type": "desktop",
            "remote_desktop_user": "jsmith",
            "project_install": False,
        }
        client.put("/api/inventory/host/Windows/DESKTOP-ABC", json=payload)
        # Must not raise
        data = read_yaml(inv_path)
        assert "all" in data
        assert "Windows" in data["all"]["children"]

    def test_group_level_vars_retained_in_file(self, client, inv_path):
        """Group-level vars (including secrets) must persist in the file even if
        not exposed by the API."""
        payload = {
            "ansible_host": "192.168.1.100",
            "host_depart": "acct",
            "host_gpu": False,
            "host_type": "desktop",
            "remote_desktop_user": "jsmith",
            "project_install": False,
        }
        client.put("/api/inventory/host/Windows/DESKTOP-ABC", json=payload)
        data = read_yaml(inv_path)
        windows_vars = data["all"]["children"]["Windows"].get("vars", {})
        assert "ansible_password" in windows_vars, (
            "Group-level secret was stripped from the YAML file on write-back"
        )
        assert "bitwarden" in str(windows_vars["ansible_password"])


# ---------------------------------------------------------------------------
# Tests: Group-level vars not returned by GET
# ---------------------------------------------------------------------------


class TestSecretsNotLeaked:
    def test_bitwarden_lookup_not_in_response(self, client):
        r = client.get("/api/inventory/raw")
        body = r.json()
        text = str(body)
        assert "bitwarden" not in text.lower(), (
            "Bitwarden secret lookup appeared in API response"
        )

    def test_ansible_password_not_in_any_host(self, client):
        r = client.get("/api/inventory/raw")
        body = r.json()
        for group_name, group_data in body["groups"].items():
            for hostname, hvars in group_data["hosts"].items():
                assert "ansible_password" not in hvars, (
                    f"ansible_password leaked in {group_name}/{hostname}"
                )

    def test_group_vars_key_absent_from_response(self, client):
        """The API response should not include a top-level 'vars' key for any group."""
        r = client.get("/api/inventory/raw")
        body = r.json()
        for group_name, group_data in body["groups"].items():
            assert "vars" not in group_data, (
                f"Group '{group_name}' exposed a 'vars' key in the API response"
            )
