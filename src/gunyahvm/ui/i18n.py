# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Gettext for the GTK frontend: English source strings, translated by system locale."""

import gettext
import os

DOMAIN = "gunyahvm"
LOCALEDIR = os.environ.get("GUNYAHVM_LOCALEDIR") or "/usr/share/locale"

translation = gettext.translation(DOMAIN, LOCALEDIR, fallback=True)
_ = translation.gettext
ngettext = translation.ngettext


def N_(message):
    """Mark a string for extraction; translate it later with _()."""
    return message
