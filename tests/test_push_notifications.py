"""Tests for push notification store, notifier, and handler integration."""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from a2a.types.a2a_pb2 import (
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    TaskPushNotificationConfig,
)

from hermes_a2a.push_notifier import PushNotificationStore, PushNotifier


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_config(
    task_id: str = "task-1",
    config_id: str = "",
    url: str = "https://example.com/webhook",
    token: str = "",
) -> TaskPushNotificationConfig:
    """Build a TaskPushNotificationConfig proto."""
    cfg = TaskPushNotificationConfig(
        task_id=task_id,
        url=url,
    )
    if config_id:
        cfg.id = config_id
    if token:
        cfg.token = token
    return cfg


# ------------------------------------------------------------------
# PushNotificationStore tests
# ------------------------------------------------------------------

class TestPushNotificationStore:
    """Unit tests for the in-memory PushNotificationStore."""

    def test_create_stores_config(self):
        store = PushNotificationStore()
        cfg = _make_config(task_id="t1", config_id="c1")
        result = store.create(cfg)
        assert result is cfg
        assert store.get("t1", "c1") is cfg

    def test_create_auto_generates_id(self):
        store = PushNotificationStore()
        cfg = _make_config(task_id="t1")
        assert not cfg.id
        result = store.create(cfg)
        assert result.id  # id was auto-generated
        assert store.get("t1", result.id) is cfg

    def test_get_returns_none_for_missing(self):
        store = PushNotificationStore()
        assert store.get("no-task", "no-config") is None

    def test_list_configs_returns_all_for_task(self):
        store = PushNotificationStore()
        store.create(_make_config(task_id="t1", config_id="c1"))
        store.create(_make_config(task_id="t1", config_id="c2"))
        store.create(_make_config(task_id="t2", config_id="c3"))
        configs = store.list_configs("t1")
        assert len(configs) == 2
        ids = {c.id for c in configs}
        assert ids == {"c1", "c2"}

    def test_list_configs_empty_for_unknown_task(self):
        store = PushNotificationStore()
        assert store.list_configs("no-task") == []

    def test_delete_removes_config(self):
        store = PushNotificationStore()
        store.create(_make_config(task_id="t1", config_id="c1"))
        assert store.delete("t1", "c1") is True
        assert store.get("t1", "c1") is None

    def test_delete_returns_false_for_missing(self):
        store = PushNotificationStore()
        assert store.delete("no-task", "no-config") is False

    def test_multiple_tasks_independent(self):
        store = PushNotificationStore()
        store.create(_make_config(task_id="t1", config_id="c1"))
        store.create(_make_config(task_id="t2", config_id="c2"))
        store.delete("t1", "c1")
        assert store.get("t1", "c1") is None
        assert store.get("t2", "c2") is not None


# ------------------------------------------------------------------
# PushNotifier tests
# ------------------------------------------------------------------

class TestPushNotifier:
    """Tests for PushNotifier webhook delivery."""

    @pytest.mark.asyncio
    async def test_deliver_posts_to_configured_url(self):
        """Verify deliver() POSTs the correct payload to the webhook URL."""
        store = PushNotificationStore()
        store.create(_make_config(
            task_id="t1", config_id="c1",
            url="https://example.com/hook", token="tok123",
        ))
        notifier = PushNotifier(store)
        mock_response = MagicMock(status_code=200)

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.is_closed = False
            MockClient.return_value = mock_client

            await notifier.deliver("t1", "completed", "done!")

            mock_client.post.assert_called_once()
            call_kwargs = mock_client.post.call_args
            assert call_kwargs[0][0] == "https://example.com/hook"
            assert call_kwargs[1]["json"]["task_id"] == "t1"
            assert call_kwargs[1]["json"]["status"] == "completed"
            assert call_kwargs[1]["json"]["message"] == "done!"
            assert call_kwargs[1]["headers"]["Authorization"] == "Bearer tok123"

    @pytest.mark.asyncio
    async def test_deliver_no_configs_does_nothing(self):
        """deliver() should be a no-op when there are no configs."""
        store = PushNotificationStore()
        notifier = PushNotifier(store)
        with patch("httpx.AsyncClient") as MockClient:
            await notifier.deliver("no-task", "completed", "msg")
            MockClient.assert_not_called()

    @pytest.mark.asyncio
    async def test_deliver_retries_on_failure(self):
        """deliver() retries up to max_retries times on connection error."""
        store = PushNotificationStore()
        store.create(_make_config(
            task_id="t1", config_id="c1", url="https://fail.example.com",
        ))
        notifier = PushNotifier(store, max_retries=3, retry_delays=(0.01, 0.01, 0.01))

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.is_closed = False
            mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
            MockClient.return_value = mock_client

            result = await notifier._post_with_retry(
                "https://fail.example.com", {"x": 1}, {}
            )
            assert result is False
            assert mock_client.post.call_count == 3

    @pytest.mark.asyncio
    async def test_deliver_succeeds_on_second_attempt(self):
        """deliver() should succeed if the second attempt works."""
        store = PushNotificationStore()
        store.create(_make_config(
            task_id="t1", config_id="c1", url="https://retry.example.com",
        ))
        notifier = PushNotifier(store, max_retries=3, retry_delays=(0.01, 0.01, 0.01))

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.is_closed = False
            mock_response_ok = MagicMock(status_code=200)
            mock_response_fail = MagicMock(status_code=500)
            mock_client.post = AsyncMock(
                side_effect=[Exception("fail"), mock_response_ok]
            )
            MockClient.return_value = mock_client

            result = await notifier._post_with_retry(
                "https://retry.example.com", {"x": 1}, {}
            )
            assert result is True
            assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_deliver_posts_to_multiple_urls(self):
        """deliver() should POST to all configured URLs for a task."""
        store = PushNotificationStore()
        store.create(_make_config(task_id="t1", config_id="c1", url="https://a.com/hook"))
        store.create(_make_config(task_id="t1", config_id="c2", url="https://b.com/hook"))
        notifier = PushNotifier(store)

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.is_closed = False
            mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
            MockClient.return_value = mock_client

            await notifier.deliver("t1", "completed", "done!")
            assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_close_cleans_up_client(self):
        notifier = PushNotifier(PushNotificationStore())
        notifier._client = AsyncMock()
        notifier._client.is_closed = False
        await notifier.close()
        notifier._client.aclose.assert_called_once()


# ------------------------------------------------------------------
# Handler integration tests
# ------------------------------------------------------------------

class TestHandlerPushNotificationMethods:
    """Test the push notification handler methods on HermesA2AHandler."""

    @pytest.fixture
    def handler(self, mock_hermes_client, task_store):
        from hermes_a2a.a2a_handler import HermesA2AHandler
        push_store = PushNotificationStore()
        return HermesA2AHandler(
            mock_hermes_client, task_store,
            push_store=push_store,
            push_notifier=PushNotifier(push_store),
        )

    @pytest.mark.asyncio
    async def test_create_push_config(self, handler):
        ctx = MagicMock()
        cfg = _make_config(task_id="t1", config_id="c1")
        result = await handler.on_create_task_push_notification_config(cfg, ctx)
        assert result.id == "c1"
        assert result.task_id == "t1"

    @pytest.mark.asyncio
    async def test_get_push_config(self, handler):
        ctx = MagicMock()
        cfg = _make_config(task_id="t1", config_id="c1")
        await handler.on_create_task_push_notification_config(cfg, ctx)
        get_req = MagicMock()
        get_req.task_id = "t1"
        get_req.id = "c1"
        result = await handler.on_get_task_push_notification_config(get_req, ctx)
        assert result is not None
        assert result.id == "c1"

    @pytest.mark.asyncio
    async def test_get_push_config_not_found(self, handler):
        ctx = MagicMock()
        get_req = MagicMock()
        get_req.task_id = "no-task"
        get_req.id = "no-config"
        result = await handler.on_get_task_push_notification_config(get_req, ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_list_push_configs(self, handler):
        ctx = MagicMock()
        await handler.on_create_task_push_notification_config(
            _make_config(task_id="t1", config_id="c1"), ctx
        )
        await handler.on_create_task_push_notification_config(
            _make_config(task_id="t1", config_id="c2"), ctx
        )
        list_req = MagicMock()
        list_req.task_id = "t1"
        result = await handler.on_list_task_push_notification_configs(list_req, ctx)
        assert isinstance(result, ListTaskPushNotificationConfigsResponse)
        assert len(result.configs) == 2

    @pytest.mark.asyncio
    async def test_delete_push_config(self, handler):
        ctx = MagicMock()
        await handler.on_create_task_push_notification_config(
            _make_config(task_id="t1", config_id="c1"), ctx
        )
        del_req = MagicMock()
        del_req.task_id = "t1"
        del_req.id = "c1"
        result = await handler.on_delete_task_push_notification_config(del_req, ctx)
        assert result is not None
        # Verify it's really gone
        get_req = MagicMock()
        get_req.task_id = "t1"
        get_req.id = "c1"
        assert await handler.on_get_task_push_notification_config(get_req, ctx) is None

    @pytest.mark.asyncio
    async def test_delete_push_config_not_found(self, handler):
        ctx = MagicMock()
        del_req = MagicMock()
        del_req.task_id = "no-task"
        del_req.id = "no-config"
        result = await handler.on_delete_task_push_notification_config(del_req, ctx)
        assert result is None
