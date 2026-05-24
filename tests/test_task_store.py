import pytest
from hermes_a2a.task_store import SQLiteTaskStore

@pytest.fixture
async def store(tmp_path):
    s = SQLiteTaskStore(str(tmp_path / 'test.db'))
    await s.init()
    yield s
    await s.close()

@pytest.mark.asyncio
async def test_save_and_get(store):
    task = {'id': 't1', 'status': {'state': 'WORKING'}}
    await store.save(task, None)
    result = await store.get('t1', None)
    assert result is not None
    assert result['id'] == 't1'

@pytest.mark.asyncio
async def test_get_missing_returns_none(store):
    assert await store.get('nonexistent', None) is None

@pytest.mark.asyncio
async def test_delete(store):
    await store.save({'id': 't2', 'status': {'state': 'COMPLETED'}}, None)
    await store.delete('t2', None)
    assert await store.get('t2', None) is None

@pytest.mark.asyncio
async def test_list(store):
    for i in range(5):
        await store.save({'id': f't{i}', 'status': {'state': 'COMPLETED'}}, None)
    assert len(await store.list(None, None)) == 5

@pytest.mark.asyncio
async def test_save_updates_existing(store):
    await store.save({'id': 't3', 'status': {'state': 'WORKING'}}, None)
    await store.save({'id': 't3', 'status': {'state': 'COMPLETED'}}, None)
    result = await store.get('t3', None)
    assert result['status']['state'] == 'COMPLETED'
