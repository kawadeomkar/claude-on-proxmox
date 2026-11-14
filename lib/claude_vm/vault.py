"""The vault, read here without Ansible: an ansible-vault file opened with the password Ansible would open it with, for
the names it sets (the hosts' check, tools/host) or what it holds (just status's snapshot, status/settings.py). One copy
of how Ansible finds and runs a password file. A vault ID's password, which only Ansible knows, opens nothing here: the
callers say so, or ask Ansible. Stdlib only when imported; yaml, cryptography and subprocess inside the function that
needs each (docs/architecture/custom-code.md §7.10)."""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Iterable, Mapping
from pathlib import Path

HEADER = b"$ANSIBLE_VAULT"


class Unopened(ValueError):
    """A vault this password does not open, or no AES256 vault at all."""


def password_files(given: str, environ: Mapping[str, str]) -> tuple[str, ...]:
    """The vault's password files, in the order Ansible tries them: the recipe's, then ANSIBLE_VAULT_PASSWORD_FILE's,
    each once. One that does not exist is the caller's to skip: it opens nothing."""
    files = [given, os.path.expanduser(environ.get("ANSIBLE_VAULT_PASSWORD_FILE", ""))]
    return tuple(one for at, one in enumerate(files) if one and one not in files[:at])


def is_script(path: str) -> bool:
    return os.access(path, os.X_OK) and not os.path.isdir(path)


def is_client(path: str) -> bool:
    """A vault password client script, which Ansible asks for a vault ID's password: Ansible's alone to run."""
    return Path(path).stem.endswith("-client")


# What each password script said in this process: run once, as it may have asked a person.
_said: dict[str, bytes] = {}


def password(path: str) -> bytes | None:
    """A password file's password, as Ansible reads one: a plain file's text, or what an executable file says, run with
    its standard input and error left to it so that it can ask. None for a client script and for a script that fails;
    OSError where the file cannot be read or run."""
    if not is_script(path):
        return Path(path).read_bytes().strip()
    if is_client(path):
        return None
    # Whole: given as .vault_pass, the name alone would be looked for on PATH.
    whole = os.path.abspath(path)
    if whole not in _said:
        import subprocess

        try:
            _said[whole] = subprocess.run([whole], stdout=subprocess.PIPE, check=True).stdout.strip(b"\r\n")
        except subprocess.SubprocessError:
            return None
    return _said[whole]


def open_text(data: bytes, password: bytes) -> bytes:
    """An ansible-vault AES256 file's text, format 1.1 or 1.2 (a vault ID's label in its header is read past). Unopened
    where the password does not open it, or it is no AES256 vault."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    lines = data.strip().splitlines()
    if not (lines and lines[0].startswith((b"$ANSIBLE_VAULT;1.1;AES256", b"$ANSIBLE_VAULT;1.2;AES256"))):
        raise Unopened("not an AES256 vault")
    salt, mac, cipher = bytes.fromhex(b"".join(lines[1:]).decode()).split(b"\n", 2)
    derived = hashlib.pbkdf2_hmac("sha256", password, bytes.fromhex(salt.decode()), 10000, 80)
    cipher = bytes.fromhex(cipher.decode())
    if not hmac.compare_digest(hmac.new(derived[32:64], cipher, hashlib.sha256).hexdigest().encode(), mac):
        raise Unopened("the vault's password does not open it")
    opened = Cipher(algorithms.AES(derived[:32]), modes.CTR(derived[64:])).decryptor()
    text = opened.update(cipher) + opened.finalize()
    return text[: -text[-1]]


def encrypted(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(len(HEADER)) == HEADER
    except OSError:
        return False


def _opened(data: bytes, password_files: Iterable[str]) -> bytes | None:
    """data, opened with the first password file that opens it, as Ansible tries them; None where none does."""
    for path in password_files:
        try:
            said = password(path)
            if said is not None:
                return open_text(data, said)
        except Exception:  # noqa: BLE001 - any refusal, a file that cannot be read among them: the next, as Ansible
            continue
    return None


class _Lenient:
    """YAML as Ansible writes it: a tag such as !vault is a value, never a key, and read as None."""

    loader = None

    @classmethod
    def load(cls, text: str):
        import yaml

        if cls.loader is None:

            class Loader(yaml.SafeLoader):
                pass

            Loader.add_multi_constructor("!", lambda loader, suffix, node: None)
            cls.loader = Loader
        return yaml.load(text, Loader=cls.loader)  # noqa: S506 - a SafeLoader, with tags read as None


def lenient_yaml(text: str) -> object:
    """YAML as the hosts' files are read: safely, a tag read as None, so a value Ansible alone reads costs nothing."""
    return _Lenient.load(text)


def names(path: Path, password_files: Iterable[str]) -> frozenset[str] | None:
    """The names a vault file sets, never its values, which go out of scope here: a plain file read as it is, an
    encrypted one opened in memory. A tag such as an inline !vault value is read as None, so its name is still one.
    None where it cannot be read or opened here - a vault ID's password, a client script's - which the caller says."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data.startswith(HEADER):
        data = _opened(data, password_files)
        if data is None:
            return None
    try:
        values = lenient_yaml(data.decode("utf-8"))
    except Exception:  # noqa: BLE001 - not YAML: Ansible says so
        return None
    return frozenset(str(name) for name in values) if isinstance(values, dict) else frozenset()


def values(paths: Iterable[str], password_files: Iterable[str]) -> dict:
    """What the vaults hold, merged as Ansible merges them, a later file's value of a name over an earlier one's: just
    status's, read with the strict loader. Unopened where no password file opens one, or one is more than plain YAML -
    what Ansible alone reads, as !unsafe - which status then asks Ansible for. A client script, or one that fails, is
    skipped as one that does not open the vault; a file that cannot be read is OSError."""
    import yaml

    files = tuple(password_files)
    merged = {}
    for file in paths:
        data = Path(file).read_bytes()
        if data.startswith(HEADER):
            for path in files:
                try:
                    said = password(path)
                    if said is None:
                        continue
                    data = open_text(data, said)
                    break
                except (OSError, Unopened):
                    continue
            else:
                raise Unopened("no password file opens the vault")
        try:
            found = yaml.load(data, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)) or {}
        except yaml.YAMLError as error:
            raise Unopened("the vault is more than plain YAML") from error
        if not isinstance(found, dict):
            raise Unopened("the vault is not a mapping")
        merged.update(found)
    return merged
