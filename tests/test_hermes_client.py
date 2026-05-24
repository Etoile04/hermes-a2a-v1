import pytest
import httpx
import respx
from hermes_a2a.hermes_client import HermesClient


@pytest.fixture
def client():
    return HermesClient(base_url='http://localhost:8642', timeout=30)


@pytest.mark.asyncio
@respx.mock
async def test_send_message_new_session(client):
    respx.post('http://localhost:8642/v1/chat/completions').mock(
        return_value=httpx.Response(200, json={
            'id': 'chatcmpl-123',
            'choices': [{'message': {'content': 'Hello from Hermes!'}}],
        })
    )
    text, session_id = await client.send_message('Hello')
    assert text == 'Hello from Hermes!'
    assert session_id is not None  # new session created


@pytest.mark.asyncio
@respx.mock
async def test_send_message_multi_turn(client):
    respx.post('http://localhost:8642/v1/chat/completions').mock(
        return_value=httpx.Response(200, json={
            'id': 'chatcmpl-456',
            'choices': [{'message': {'content': 'Follow-up response'}}],
            'session_id': 'sess-abc'
        })
    )
    text, session_id = await client.send_message('Follow up', session_id='sess-abc')
    assert text == 'Follow-up response'


@pytest.mark.asyncio
@respx.mock
async def test_health_check_ok(client):
    respx.get('http://localhost:8642/health').mock(
        return_value=httpx.Response(200, json={'status': 'ok'})
    )
    assert await client.health_check() is True


@pytest.mark.asyncio
@respx.mock
async def test_health_check_fail(client):
    respx.get('http://localhost:8642/health').mock(
        side_effect=httpx.ConnectError('Connection refused')
    )
    assert await client.health_check() is False
