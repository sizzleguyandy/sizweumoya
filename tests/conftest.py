"""Every test writes runner record files into its own temporary folder."""
import time
from dataclasses import replace

import pytest

from app import config, record


@pytest.fixture(autouse=True)
def _records_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "settings", replace(config.settings, records_dir=str(tmp_path / "records")))
    yield
    # Let background rebuilds finish while the temporary folder is still the target.
    deadline = time.time() + 5
    while record._running and time.time() < deadline:
        time.sleep(0.01)
