import os
import shutil
import tempfile

import pytest

from qs_hybrid.control.policy import Caller
from qs_hybrid.site import Site

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def site():
    tmp = tempfile.mkdtemp()
    s = Site(os.path.join(ROOT, "config", "site.example.yaml"), tmp)
    yield s
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def operator():
    return Caller("operator:test", "operator")


@pytest.fixture
def engineer():
    return Caller("engineer:test", "engineer")


@pytest.fixture
def viewer():
    return Caller("viewer:test", "viewer")


@pytest.fixture
def ai():
    return Caller("ai:test", "ai")


@pytest.fixture
def automation():
    return Caller("automation:test", "automation")
