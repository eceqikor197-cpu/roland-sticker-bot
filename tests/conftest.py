import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("PLOTTER_BOT_TOKEN", "123456:dummy_test_token")

import pytest


@pytest.fixture(autouse=True)
def isolate_storage_db(tmp_path: Path):
    from storage.database import init_storage, set_custom_db_path
    db_file = tmp_path / "test_storage.db"
    set_custom_db_path(db_file)
    init_storage(db_file)
    yield
    set_custom_db_path(None)
