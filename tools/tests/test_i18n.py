# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Translations are complete and source code stays in the English source language."""

import gettext
import os
from pathlib import Path
import re
import subprocess
import sys

import translations

REPO = Path(__file__).resolve().parents[2]
# CJK punctuation, kana, ideographs (incl. extension A), Hangul and full-width forms.
CJK = re.compile("[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uff00-\uffef]")


def python_sources():
    files = sorted((REPO / "src").rglob("*.py"))
    files += [REPO / "bin/gunyahvm", REPO / "libexec/gunyahvm-helper"]
    return files


def test_translation_catalogues_are_complete_and_current():
    assert translations.problems() == []


def test_source_code_contains_no_cjk_text():
    offenders = [
        f"{path.relative_to(REPO)}:{number}: {line.strip()}"
        for path in python_sources()
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if CJK.search(line)
    ]
    assert offenders == []


def test_compiled_catalogue_translates_and_is_reproducible(tmp_path):
    first = translations.compile_all(tmp_path / "a")
    second = translations.compile_all(tmp_path / "b")
    assert [p.read_bytes() for p in first] == [p.read_bytes() for p in second]
    catalogue = gettext.translation("gunyahvm", tmp_path / "a", languages=["zh_CN"])
    assert catalogue.gettext("New Virtual Machine") == "新建虚拟机"
    assert catalogue.ngettext("{count} core", "{count} cores", 4).format(count=4) == "4 核"
    english = gettext.translation("gunyahvm", tmp_path / "a", languages=["en"], fallback=True)
    assert english.gettext("New Virtual Machine") == "New Virtual Machine"


def test_frontend_follows_the_system_language(tmp_path):
    translations.compile_all(tmp_path)
    probe = (
        "from gunyahvm.ui import text\n"
        "print(text.state({'state': 'running'})[0])\n"
        "print(text.explain('Battery below 20%'))\n"
    )

    def run(language):
        env = {
            **os.environ, "LANGUAGE": language, "LANG": "C.UTF-8",
            "GUNYAHVM_LOCALEDIR": str(tmp_path), "PYTHONPATH": str(REPO / "src"),
        }
        return subprocess.check_output([sys.executable, "-c", probe], env=env, text=True)

    assert run("zh_CN").splitlines() == ["运行中", "电量低于 20%，请充电后再试。"]
    assert run("en").splitlines() == [
        "Running", "The battery is below 20%. Charge the device and try again.",
    ]


def test_known_backend_failures_get_a_plain_explanation():
    from gunyahvm.ui import text

    for message, expected in (
        ("another VM owned by this user is running", "Another virtual machine"),
        ("Device is throttling: thermal_zone3 (cpu), 96 C >= passive 95 C", "too hot"),
        ("need MemAvailable >= 6400 MiB", "6.25 GB"),
        ("polkit did not authorize io.github.yzddmr6.gunyahvm.run", "Permission"),
        ("no read/write access to /dev/gunyah; require an active local seat", "System Check"),
        ("Stop the VM before changing its settings", "Shut down"),
        ("Guest agent is not ready; inspect console or use stop --force", "force it off"),
    ):
        assert expected in text.explain(message), message
    assert text.explain("some new failure") is None


def test_only_a_guest_that_never_reported_ready_counts_as_starting():
    from gunyahvm.ui import text

    linux, windows = {"os": "linux"}, {"os": "windows11"}
    assert text.starting(linux, {"state": "running"})
    assert not text.starting(linux, {"state": "running", "guest_ready": True})
    assert not text.starting(linux, {"state": "running", "guest_ready": False})  # agent reported an error
    assert not text.starting(linux, {"state": "starting"})
    assert not text.starting(windows, {"state": "running"})  # Windows shuts down over SSH, not the agent


def test_setup_problems_are_visible_only_while_running():
    from gunyahvm.ui import text

    failed = {"status": "error - done", "problems": ["package_update_upgrade_install"]}
    assert text.setup_result({"state": "running", "guest_setup": failed}) == (
        "error", ["package_update_upgrade_install"])
    assert text.state({"state": "running", "guest_setup": failed})[1] == "warning"
    assert text.setup_result({"state": "running", "guest_setup": {"status": "degraded done"}})[0] == "warning"
    assert text.state({"state": "running", "guest_setup": {"status": "degraded done"}})[1] == "running"
    assert text.setup_result({"state": "running", "guest_setup": {"status": "done"}}) is None
    assert text.setup_result({"state": "running"}) is None  # agents before 1.1.0
    assert text.setup_result({"state": "stopped", "guest_setup": failed}) is None
