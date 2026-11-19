import pytest

from dropbox_organizer.store import ScanStore


@pytest.fixture
def store(tmp_path):
    s = ScanStore(tmp_path / "scan.sqlite")
    yield s
    s.close()

