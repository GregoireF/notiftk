"""
Shared pytest fixtures for the full test suite.

clean_webhooks (autouse, async):
  - Redirects the SQLite DB to a per-test temp directory so tests never
    touch data/webhooks.db and the lifespan's restore_webhooks() always
    starts from an empty database.
  - Cancels and awaits all pending poll tasks during teardown, while the
    asyncio event loop is still alive — preventing the "Task was destroyed
    but it is pending!" warning that occurs when tasks are cancelled after
    loop shutdown.
"""

import asyncio
import pytest

import api.webhooks as wh
from api.webhooks import _watchers


@pytest.fixture(autouse=True)
async def clean_webhooks(tmp_path):
    original_path = wh._DB_PATH
    wh._DB_PATH = tmp_path / "webhooks_test.db"
    _watchers.clear()

    yield

    # Teardown runs while the event loop is still open (async fixture).
    # Properly cancel and await every pending task before the loop closes.
    pending = [w.task for w in list(_watchers.values()) if w.task and not w.task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    _watchers.clear()
    wh._DB_PATH = original_path
