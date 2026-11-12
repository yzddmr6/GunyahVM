# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Real pipe backpressure, binary/oversize output, timeout and guest capability limits."""

import sys
from pathlib import Path
import pytest
from gunyahvm import clipboard


def test_unicode_exchange_with_pipe_backpressure():
    text = "中文\nclipboard\t" * 50000
    result = clipboard.exchange([sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"], text.encode())
    assert result == text


def test_output_limit_does_not_trust_guest():
    with pytest.raises(ValueError, match="exceeds"):
        clipboard.exchange([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 2000000)"])
    with pytest.raises(UnicodeDecodeError):
        clipboard.exchange([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff')"])
    with pytest.raises(ValueError, match="NUL"):
        clipboard.exchange([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\0')"])


def test_stuck_guest_and_failed_guest_do_not_return_text():
    with pytest.raises(ValueError, match="timed out"):
        clipboard.exchange([sys.executable, "-c", "import time; time.sleep(60)"], timeout=.05)
    with pytest.raises(ValueError, match="unavailable"):
        clipboard.exchange([sys.executable, "-c", "print('not success'); raise SystemExit(1)"])


def test_windows_service_session_is_not_a_desktop_clipboard():
    with pytest.raises(ValueError, match="desktop-session"):
        clipboard.transfer(Path("/unused"), {"os": "windows11"}, "probe")
    with pytest.raises(ValueError, match="UTF-8"):
        clipboard.transfer(Path("/unused"), {"os": "linux"}, "write", "\0")
