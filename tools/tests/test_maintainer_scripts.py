# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Upgrades never keep running bytecode compiled from an earlier version's modules."""

import os
from pathlib import Path
import py_compile
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
EPOCH = 1794009600  # the build stamps this mtime on every packaged file


def installed(root, old, new):
    """New module text over bytecode from an old text of the same size and mtime."""
    assert len(old) == len(new)
    modules = root / "usr/lib/gunyahvm/python"
    module = modules / "gunyahvm/__init__.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(old)
    os.utime(module, (EPOCH, EPOCH))
    # What a root process importing the old version without -B left behind.
    py_compile.compile(str(module), doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    module.write_text(new)
    os.utime(module, (EPOCH, EPOCH))
    return modules


def version(modules):
    code = f"import sys; sys.path.insert(0, {str(modules)!r}); import gunyahvm; print(gunyahvm.__version__)"
    return subprocess.run([sys.executable, "-IB", "-c", code], check=True,
                          capture_output=True, text=True).stdout.strip()


def run(script, action, root, tmp_path):
    # Stand-ins for the host commands that configure would otherwise call.
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    for name in ("update-desktop-database", "gtk-update-icon-cache", "systemd-sysusers",
                 "systemctl", "udevadm", "systemd-tmpfiles", "apparmor_parser"):
        (stubs / name).write_text("#!/bin/sh\nexit 0\n")
        (stubs / name).chmod(0o755)
    env = {**os.environ, "DPKG_ROOT": str(root), "PATH": f"{stubs}:{os.environ['PATH']}"}
    return subprocess.run(["sh", str(REPO / "debian" / script), action, "1.3.0-1"],
                          env=env, capture_output=True, text=True)


@pytest.mark.parametrize("script,action", [
    ("postinst", "configure"), ("prerm", "upgrade"), ("prerm", "remove"),
    ("prerm", "deconfigure")])
def test_same_size_and_mtime_bytecode_is_removed(tmp_path, script, action):
    root = tmp_path / "root"
    modules = installed(root, '__version__ = "1.3.0"\n', '__version__ = "1.4.0"\n')
    assert version(modules) == "1.3.0"  # the stale bytecode still wins
    other = modules / "gunyahvm/ui/__pycache__"
    other.mkdir(parents=True)
    (other / "text.cpython-312.pyc").write_bytes(b"stale")
    result = run(script, action, root, tmp_path)
    assert result.returncode == 0, result.stderr
    assert not list(modules.rglob("__pycache__"))
    assert version(modules) == "1.4.0"
    assert (modules / "gunyahvm/__init__.py").is_file()


def test_configure_without_installed_modules_succeeds(tmp_path):
    result = run("postinst", "configure", tmp_path / "empty", tmp_path)
    assert result.returncode == 0, result.stderr
