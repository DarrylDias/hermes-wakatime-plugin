"""Consented bootstrap for wakatime-cli.

Downloads the official GitHub release asset for the current OS/arch, verifies it against the
release's SHA-256 checksums file, installs it atomically at the conventional location, and
verifies the binary with --version. Never runs automatically: `hermes wakatime install-cli`
prints the plan first and requires explicit consent unless --yes is passed.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Optional

from . import wakatime_paths as paths

_REPO = "wakatime/wakatime-cli"
_RELEASE_BASE = f"https://github.com/{_REPO}/releases/download"
_LATEST_API = f"https://api.github.com/repos/{_REPO}/releases/latest"
_USER_AGENT = f"{paths.plugin_string()} install-cli"

# system -> release slug
_SYSTEM_SLUGS = {
    "windows": "windows",
    "win32": "windows",
    "darwin": "darwin",
    "macos": "darwin",
    "mac": "darwin",
    "linux": "linux",
    "freebsd": "freebsd",
    "netbsd": "netbsd",
    "openbsd": "openbsd",
}
# machine -> release arch token
_ARCH_TOKENS = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "x64": "amd64",
    "x86-64": "amd64",
    "arm64": "arm64",
    "aarch64": "arm64",
    "i386": "386",
    "i686": "386",
    "x86": "386",
    "arm": "arm",
    "armv7l": "arm",
    "armv6l": "arm",
    "riscv64": "riscv64",
}

_MISSING_ARCH = {
    "windows": {"arm", "riscv64"},
    "darwin": {"386", "arm", "riscv64"},
    "freebsd": {"arm64", "riscv64"},
    "netbsd": {"arm64", "riscv64"},
    "openbsd": {"riscv64"},
}


class InstallerError(RuntimeError):
    """A bootstrap failure with a user-facing message."""


def _normalize_version(version: Optional[str]) -> Optional[str]:
    if not version or str(version).strip().lower() == "latest":
        return None
    value = str(version).strip()
    if value.startswith("v"):
        value = value[1:]
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise InstallerError(f"expected a release tag like v2.28.4 or 2.28.4, got {version!r}")
    return value


def _asset_from_platform(system: Optional[str] = None, machine: Optional[str] = None) -> tuple[str, str, str]:
    system_name = (system or platform.system()).strip().lower()
    machine_name = machine if machine is not None else platform.machine()
    slug = _SYSTEM_SLUGS.get(system_name)
    if not slug:
        raise InstallerError(
            f"unsupported OS {system_name!r}; WakaTime CLI releases cover Windows, macOS, Linux, "
            "FreeBSD, NetBSD and OpenBSD."
        )
    arch = _ARCH_TOKENS.get((machine_name or "").strip().lower())
    if not arch:
        raise InstallerError(
            f"unsupported CPU architecture {machine_name!r}; release assets are amd64/arm64/386/arm/riscv64."
        )
    if arch in _MISSING_ARCH.get(slug, set()):
        raise InstallerError(f"no WakaTime CLI release asset for {slug}-{arch}")
    executable = slug  # release archive names use the slug
    return slug, arch, executable


def asset_name(system: Optional[str] = None, machine: Optional[str] = None) -> str:
    slug, arch, _ = _asset_from_platform(system, machine)
    return f"wakatime-cli-{slug}-{arch}.zip"


def executable_name(system: Optional[str] = None) -> str:
    slug, _, _ = _asset_from_platform(system)
    return "wakatime-cli.exe" if slug == "windows" else "wakatime-cli"


def _request(url: str, *, timeout: float = 30.0, headers: Optional[dict] = None) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/octet-stream", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise InstallerError(f"download failed: HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise InstallerError(f"download failed: {exc.reason} for {url}") from exc


def resolve_latest_tag(timeout: float = 30.0) -> str:
    body = _request(_LATEST_API, timeout=timeout, headers={"Accept": "application/vnd.github+json"})
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise InstallerError("GitHub API returned non-JSON") from exc
    tag = payload.get("tag_name") if isinstance(payload, dict) else None
    if not tag or not re.fullmatch(r"v\d+\.\d+\.\d+", str(tag)):
        raise InstallerError(f"could not determine the latest WakaTime CLI release tag ({tag!r})")
    return tag


def expected_sha256(tag: str, asset: str, timeout: float = 30.0) -> str:
    url = f"{_RELEASE_BASE}/{tag}/checksums_sha256.txt"
    body = _request(url, timeout=timeout, headers={"Accept": "text/plain"})
    for line in body.decode("utf-8", errors="replace").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == asset and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    raise InstallerError(f"release checksums file has no SHA-256 for {asset}")


def _download_zip(tag: str, asset: str, timeout: float = 120.0) -> bytes:
    url = f"{_RELEASE_BASE}/{tag}/{asset}"
    return _request(url, timeout=timeout)


def _extract_executable(archive: bytes, expected_name: str) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            names = [name for name in zf.namelist() if not name.endswith("/")]
            candidates = [name for name in names if Path(name).name.lower() == expected_name.lower()]
            if not candidates:
                candidates = [
                    name for name in names
                    if "wakatime-cli" in Path(name).name.lower()
                    and not name.lower().endswith((".txt", ".zip", ".sha256", ".checksums"))
                ]
            if not candidates:
                raise InstallerError("archive did not contain a wakatime-cli executable")
            # Prefer the exact platform executable name when one exists among candidates.
            exact = [name for name in candidates if expected_name.lower() in Path(name).name.lower()]
            chosen = exact[0] if exact else candidates[0]
            return zf.read(chosen)
    except zipfile.BadZipFile as exc:
        raise InstallerError("downloaded asset is not a valid zip archive") from exc


def _installed_version(destination: Path) -> Optional[str]:
    if not destination.is_file():
        return None
    try:
        proc = subprocess.run(
            [str(destination), "--version"],
            capture_output=True, text=True, timeout=20.0, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    text = (proc.stdout or "").strip()
    return text.splitlines()[-1].strip() if text else None


def install_plan(
    version: Optional[str] = None,
    system: Optional[str] = None,
    machine: Optional[str] = None,
    destination: Optional[Path] = None,
) -> dict:
    slug, arch, executable = _asset_from_platform(system, machine)
    tag = resolve_latest_tag() if _normalize_version(version) is None else "v" + (_normalize_version(version) or "")
    asset = f"wakatime-cli-{slug}-{arch}.zip"
    digest = expected_sha256(tag, asset)
    dest_dir = (destination or (paths.wakatime_home() / ".wakatime")).expanduser()
    exe_name = executable_name(system)
    return {
        "tag": tag,
        "system": slug,
        "arch": arch,
        "asset": asset,
        "asset_url": f"{_RELEASE_BASE}/{tag}/{asset}",
        "checksums_url": f"{_RELEASE_BASE}/{tag}/checksums_sha256.txt",
        "sha256": digest,
        "destination_dir": str(dest_dir),
        "executable_name": exe_name,
        "executable_path": str(dest_dir / exe_name),
        "existing_path": str(paths.find_cli()) if paths.find_cli() else None,
        "existing_version": _installed_version(dest_dir / exe_name),
    }


def perform_install(
    version: Optional[str] = None,
    destination: Optional[Path] = None,
    force: bool = False,
    system: Optional[str] = None,
    machine: Optional[str] = None,
) -> dict:
    plan = install_plan(version, system=system, machine=machine, destination=destination)
    dest_dir = Path(plan["destination_dir"])
    dest = dest_dir / plan["executable_name"]
    if dest.is_file() and not force:
        current = _installed_version(dest) or "unknown"
        if f"{current}".strip().lower() == plan["tag"].lower() or plan["tag"].lower() in f"{current}".lower():
            return {"already_installed": True, "install_plan": plan, "destination": str(dest)}
    dest_dir.mkdir(parents=True, exist_ok=True)
    archive = _download_zip(plan["tag"], plan["asset"])
    if hashlib.sha256(archive).hexdigest().lower() != plan["sha256"]:
        raise InstallerError(f"SHA-256 mismatch for {plan['asset']}")
    executable = _extract_executable(archive, plan["executable_name"])
    backup: Optional[Path] = None
    if dest.is_file():
        backup = dest.with_name(dest.name + ".bak-hermes-install")
        shutil.copy2(dest, backup)
    fd, temp_name = tempfile.mkstemp(prefix=dest.name + ".", suffix=".tmp", dir=str(dest_dir))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(executable)
        os.replace(temp_name, dest)
        if os.name != "nt":
            os.chmod(dest, 0o755)
    except OSError as exc:
        raise InstallerError(f"could not write {dest}: {exc}") from exc
    version_text = _installed_version(dest)
    expected = plan["tag"].lstrip("v")
    if not version_text or expected not in version_text:
        if backup and backup.is_file():
            os.replace(backup, dest)
        else:
            try:
                dest.unlink()
            except OSError:
                pass
        raise InstallerError(f"installed binary failed verification (--version returned {version_text!r})")
    return {
        "already_installed": False,
        "install_plan": plan,
        "destination": str(dest),
        "installed_version": version_text,
        "sha256_verified": True,
        "backup": str(backup) if backup else None,
    }
