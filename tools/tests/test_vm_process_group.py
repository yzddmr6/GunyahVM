# SPDX-License-Identifier: MIT
# Copyright (C) 2026 yzddMr6
"""crosvm's exit cleanup kills its own process group; the supervisor must survive.

Run: python3 -m pytest tools/tests/test_vm_process_group.py
"""

from pathlib import Path
import subprocess
import sys
import textwrap

CLI = Path(__file__).resolve().parents[2] / "src/gunyahvm/cli.py"
# A stand-in VMM doing what crosvm's kill_process_group() does: kill(0, SIGKILL).
VMM = [sys.executable, "-c", "import os, signal; os.killpg(0, signal.SIGKILL)"]


def supervisor(spawn):
    """Run a stand-in supervisor in its own session, as gunyahvm starts it."""
    code = textwrap.dedent(f"""
        import subprocess, sys
        sys.path.insert(0, {str(CLI.parents[1])!r})
        from gunyahvm import core as cli
        vmm = {VMM!r}
        proc = {spawn}
        proc.wait()
        print('supervisor recorded', proc.returncode)
    """)
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        start_new_session=True,
        timeout=30,
    )


def test_shared_group_kills_the_supervisor():
    result = supervisor("subprocess.Popen(vmm)")
    assert result.returncode == -9
    assert "supervisor recorded" not in result.stdout


def test_spawn_vmm_keeps_the_supervisor_alive():
    result = supervisor("cli.spawn_vmm(vmm, None, None, None)")
    assert result.returncode == 0, result.stderr
    assert "supervisor recorded -9" in result.stdout
