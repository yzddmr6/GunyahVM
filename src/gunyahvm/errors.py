# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
class Failure(RuntimeError):
    pass


class RequiresUpdate(ValueError):
    """An existing VM belongs to a guest profile unavailable in this version."""
