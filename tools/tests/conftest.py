# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def pytest_addoption(parser):
    parser.addoption(
        "--input-cache",
        type=Path,
        help="Verified release cache for payload integration tests (offline)",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "inputs: requires the locked release input cache"
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--input-cache") is None:
        selected = [item for item in items if item.get_closest_marker("inputs") is None]
        deselected = [
            item for item in items if item.get_closest_marker("inputs") is not None
        ]
        items[:] = selected
        config.hook.pytest_deselected(items=deselected)


@pytest.fixture(scope="session")
def input_cache(request):
    return request.config.getoption("--input-cache")


@pytest.fixture(scope="session")
def verified_inputs(input_cache, tmp_path_factory):
    from prebuilt_inputs import prepare

    assert input_cache is not None, "pass --input-cache for payload integration tests"
    return prepare(
        input_cache, tmp_path_factory.mktemp("inputs") / "assets", offline=True
    )
