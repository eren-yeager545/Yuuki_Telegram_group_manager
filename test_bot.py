import asyncio
import pytest
from bot import ensure_event_loop

def test_ensure_event_loop_when_none_set():
    asyncio.set_event_loop(None)
    loop = ensure_event_loop()
    assert loop is not None
    assert not loop.is_closed()
    assert asyncio.get_event_loop() == loop

def test_ensure_event_loop_when_closed():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.close()

    new_loop = ensure_event_loop()
    assert new_loop is not None
    assert not new_loop.is_closed()
    assert new_loop != loop
    assert asyncio.get_event_loop() == new_loop

def test_ensure_event_loop_when_already_active():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    existing_loop = ensure_event_loop()
    assert existing_loop == loop
    assert not existing_loop.is_closed()

import store
from bot import global_error_handler
from unittest.mock import MagicMock

def test_get_conn():
    store.init_db()
    with store.get_conn() as c:
        row = c.execute("SELECT 1").fetchone()
        assert row[0] == 1
    # Check that check_chat_quota works without NameError
    assert store.check_chat_quota(1001, 'notes') is True

@pytest.mark.asyncio
async def test_global_error_handler(caplog):
    context = MagicMock()
    context.error = ValueError("Test error exception")
    update = MagicMock()
    with caplog.at_level("ERROR"):
        await global_error_handler(update, context)
    assert "Exception while handling an update:" in caplog.text
    assert "Test error exception" in caplog.text
