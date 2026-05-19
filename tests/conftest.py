"""
Shared pytest fixtures for the full test suite.

clean_state (autouse, async):
  - Redirects the SQLite DB to a per-test temp directory so tests never
    touch data/webhooks.db and the lifespan's restore_webhooks() always
    starts from an empty database.
  - Clears the keys in-memory rate-limit log between tests.
  - Cancels and awaits all pending poll tasks during teardown, while the
    asyncio event loop is still alive — preventing the "Task was destroyed
    but it is pending!" warning that occurs when tasks are cancelled after
    loop shutdown.
"""

import asyncio
import pytest

import api.webhooks as wh
import api.keys as keys_module
from api.webhooks import _watchers


@pytest.fixture(autouse=True)
async def clean_state(tmp_path):
    # Redirect SQLite to an isolated temp file for each test
    original_wh_path = wh._DB_PATH
    original_keys_path = keys_module.DB_PATH
    test_db = tmp_path / "test.db"
    wh._DB_PATH = test_db
    keys_module.DB_PATH = test_db

    # Clear in-memory rate-limit state for key generation
    keys_module._gen_log.clear()
    _watchers.clear()

    yield

    # Teardown: cancel all pending webhook poll tasks before the event loop closes
    pending = [w.task for w in list(_watchers.values()) if w.task and not w.task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    _watchers.clear()
    keys_module._gen_log.clear()
    wh._DB_PATH = original_wh_path
    keys_module.DB_PATH = original_keys_path
