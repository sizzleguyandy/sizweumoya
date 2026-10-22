"""Every test writes runner record files into its own temporary folder."""
from dataclasses import replace

import pytest

from app import config, record


@pytest.fixture(autouse=True)
def _records_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "settings", replace(config.settings, records_dir=str(tmp_path / "records")))
