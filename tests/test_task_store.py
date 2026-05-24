import pytest

# `task_store` fixture is provided by conftest.py

@pytest.mark.asyncio
async def test_save_and_get(task_store):
    task = {'id': 't1', 'status': {'state': 'WORKING'}}
    await task_store.save(task, None)
    result = await task_store.get('t1', None)
    assert result is not None
    assert result['id'] == 't1'

@pytest.mark.asyncio
async def test_get_missing_returns_none(task_store):
    assert await task_store.get('nonexistent', None) is None

@pytest.mark.asyncio
async def test_delete(task_store):
    await task_store.save({'id': 't2', 'status': {'state': 'COMPLETED'}}, None)
    await task_store.delete('t2', None)
    assert await task_store.get('t2', None) is None

@pytest.mark.asyncio
async def test_list(task_store):
    for i in range(5):
        await task_store.save({'id': f't{i}', 'status': {'state': 'COMPLETED'}}, None)
    assert len(await task_store.list(None, None)) == 5

@pytest.mark.asyncio
async def test_save_updates_existing(task_store):
    await task_store.save({'id': 't3', 'status': {'state': 'WORKING'}}, None)
    await task_store.save({'id': 't3', 'status': {'state': 'COMPLETED'}}, None)
    result = await task_store.get('t3', None)
    assert result['status']['state'] == 'COMPLETED'
