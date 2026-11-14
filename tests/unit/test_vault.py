"""lib/claude_vm/vault.py, the one reader of the vault without Ansible: held to the names Ansible's own VaultLib reads,
file by file, since the hosts' check read them through VaultLib until it moved here (docs/architecture/custom-code.md
§7.10). Each test's vaults and password files are its own, with canned passwords."""

from __future__ import annotations

from pathlib import Path

import pytest
from ansible.parsing.vault import VaultLib, VaultSecret
from harness import _executable

from claude_vm import vault

PASSWORD = b"the-vault-password"
TEXT = b"vault_proxmox_api_token_secret: a-secret\nvault_vm_password: another\n"
# A plain vault holding a value Ansible alone decrypts: its name is still the vault's.
INLINE = (
    b"vault_host_proxmox_api_token_secret: !vault |\n  $ANSIBLE_VAULT;1.1;AES256\n  3132\nvault_vm_password: plain\n"
)


def _sealed(text: bytes, password: bytes = PASSWORD, label: str | None = None) -> bytes:
    secret = VaultSecret(password)
    return bytes(VaultLib(secrets=[("default", secret)]).encrypt(text, secret, vault_id=label))


def _as_vaultlib(path: Path, password_files: list[str]) -> frozenset[str] | None:
    """The names as the hosts' check read them before: VaultLib with each password, then YAML with tags as None."""
    data = path.read_bytes()
    if data.startswith(b"$ANSIBLE_VAULT"):
        for file in password_files:
            said = vault.password(file)
            if said is None:
                continue
            try:
                data = VaultLib([("default", VaultSecret(said))]).decrypt(data)
                break
            except Exception:  # noqa: BLE001 - the next file, as Ansible tries them
                continue
        else:
            return None
    values = vault.lenient_yaml(data.decode())
    return frozenset(map(str, values)) if isinstance(values, dict) else frozenset()


@pytest.fixture
def password_file(tmp_path: Path) -> str:
    path = tmp_path / "password"
    path.write_bytes(PASSWORD + b"\n")
    return str(path)


# Each made in the test: a vault is sealed with a fresh salt, which as a parameter would differ from worker to worker.
VAULTS = {
    "plain": lambda: TEXT,
    "inline": lambda: INLINE,
    "1.1": lambda: _sealed(TEXT),
    # A vault ID's label in the header: Ansible tries every password beside the label's, and so does the reader.
    "1.2": lambda: _sealed(TEXT, label="homelab"),
    "sealed-inline": lambda: _sealed(INLINE),
    "another-password": lambda: _sealed(TEXT, password=b"not-the-vault-password"),
    "not-a-mapping": lambda: _sealed(b"- a\n- b\n"),
}


@pytest.mark.parametrize("name", list(VAULTS))
def test_the_names_are_those_vaultlib_reads(tmp_path: Path, password_file: str, name: str) -> None:
    path = tmp_path / f"{name}.yml"
    path.write_bytes(VAULTS[name]())
    assert vault.names(path, [password_file]) == _as_vaultlib(path, [password_file])


def test_the_names_of_each_vault_and_nothing_of_their_values(tmp_path: Path, password_file: str) -> None:
    sealed = tmp_path / "sealed.yml"
    sealed.write_bytes(_sealed(TEXT, label="homelab"))
    inline = tmp_path / "inline.yml"
    inline.write_bytes(INLINE)
    assert vault.names(sealed, [password_file]) == {"vault_proxmox_api_token_secret", "vault_vm_password"}
    assert vault.names(inline, []) == {"vault_host_proxmox_api_token_secret", "vault_vm_password"}
    # Opened by no password here: none, which the caller says.
    assert vault.names(sealed, []) is None
    assert vault.names(sealed, [str(tmp_path / "missing")]) is None
    assert vault.names(tmp_path / "missing.yml", [password_file]) is None


def test_a_password_file_is_tried_in_ansible_s_order_and_a_client_script_is_ansible_s(tmp_path: Path) -> None:
    sealed = tmp_path / "sealed.yml"
    sealed.write_bytes(_sealed(TEXT))
    wrong = tmp_path / "wrong"
    wrong.write_text("not-it\n")
    script = _executable(tmp_path / "vault-pass.sh", f"#!/bin/sh\necho {PASSWORD.decode()}\n")
    client = _executable(tmp_path / "vault-pass-client.sh", f"#!/bin/sh\necho {PASSWORD.decode()}\n")
    failing = _executable(tmp_path / "failing.sh", "#!/bin/sh\nexit 3\n")
    assert vault.names(sealed, [str(wrong), str(script)]) == {"vault_proxmox_api_token_secret", "vault_vm_password"}
    assert vault.password(str(client)) is None and vault.names(sealed, [str(client)]) is None
    assert vault.password(str(failing)) is None and vault.names(sealed, [str(failing)]) is None


def test_the_password_files_are_the_recipe_s_then_the_environment_s_each_once() -> None:
    environ = {"ANSIBLE_VAULT_PASSWORD_FILE": "/a/pass"}
    assert vault.password_files(".vault_pass", environ) == (".vault_pass", "/a/pass")
    assert vault.password_files("/a/pass", environ) == ("/a/pass",)
    assert vault.password_files("", {}) == ()


def test_the_values_are_read_strictly_and_say_what_opens_nothing(tmp_path: Path, password_file: str) -> None:
    sealed = tmp_path / "sealed.yml"
    sealed.write_bytes(_sealed(TEXT))
    assert vault.values([str(sealed)], [password_file])["vault_vm_password"] == "another"
    with pytest.raises(vault.Unopened, match="no password file opens the vault"):
        vault.values([str(sealed)], [])
    inline = tmp_path / "inline.yml"
    inline.write_bytes(INLINE)
    # The strict loader refuses a tag: just status then asks Ansible.
    with pytest.raises(vault.Unopened, match="more than plain YAML"):
        vault.values([str(inline)], [password_file])
