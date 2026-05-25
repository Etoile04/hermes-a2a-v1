"""Tests for MessageParser — multi-part message parsing for A2A protocol."""

from __future__ import annotations

import pytest
from google.protobuf import struct_pb2

from a2a.types.a2a_pb2 import Message, Part, SendMessageRequest


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_request(*parts: Part, context_id: str = "ctx-1") -> SendMessageRequest:
    """Build a SendMessageRequest with the given Parts."""
    return SendMessageRequest(
        message=Message(
            role="ROLE_USER",
            parts=list(parts),
            context_id=context_id,
        ),
    )


# ------------------------------------------------------------------
# Unit tests for MessageParser
# ------------------------------------------------------------------

class TestMessageParser:
    """Unit tests for MessageParser.extract_text_for_hermes."""

    def _make_parser(self):
        from hermes_a2a.message_parser import MessageParser
        return MessageParser()

    # ---- Single text Part ----

    def test_single_text_part(self):
        parser = self._make_parser()
        req = _make_request(Part(text="Hello world"))
        result = parser.extract_text_for_hermes(req)
        assert result == "Hello world"

    # ---- Multiple text Parts ----

    def test_multiple_text_parts(self):
        parser = self._make_parser()
        req = _make_request(Part(text="Hello "), Part(text="world"))
        result = parser.extract_text_for_hermes(req)
        assert result == "Hello world"

    # ---- URL Part ----

    def test_url_part(self):
        parser = self._make_parser()
        req = _make_request(
            Part(
                url="http://example.com/f.pdf",
                filename="f.pdf",
                media_type="application/pdf",
            )
        )
        result = parser.extract_text_for_hermes(req)
        assert result == "[FILE: f.pdf (application/pdf) - http://example.com/f.pdf]"

    def test_url_part_without_filename(self):
        parser = self._make_parser()
        req = _make_request(
            Part(url="http://example.com/x", media_type="image/png")
        )
        result = parser.extract_text_for_hermes(req)
        assert result == "[FILE: unknown (image/png) - http://example.com/x]"

    def test_url_part_without_media_type(self):
        parser = self._make_parser()
        req = _make_request(Part(url="http://example.com/x", filename="doc.txt"))
        result = parser.extract_text_for_hermes(req)
        assert result == "[FILE: doc.txt (unknown) - http://example.com/x]"

    # ---- Data Part ----

    def test_data_part_with_utf8_content(self):
        parser = self._make_parser()
        data_val = struct_pb2.Value(string_value='{"key": "value"}')
        req = _make_request(Part(data=data_val, media_type="application/json"))
        result = parser.extract_text_for_hermes(req)
        assert result == '[DATA: application/json - {"key": "value"}]'

    def test_data_part_without_media_type(self):
        parser = self._make_parser()
        data_val = struct_pb2.Value(string_value="some data")
        req = _make_request(Part(data=data_val))
        result = parser.extract_text_for_hermes(req)
        assert result == "[DATA: binary - some data]"

    def test_data_part_with_non_string_value(self):
        """Data part with a non-string Value (e.g., number) should still work."""
        parser = self._make_parser()
        data_val = struct_pb2.Value(number_value=42.0)
        req = _make_request(Part(data=data_val, media_type="text/plain"))
        result = parser.extract_text_for_hermes(req)
        # Should convert the number to string
        assert "42" in result

    # ---- Raw Part ----

    def test_raw_part(self):
        parser = self._make_parser()
        req = _make_request(Part(raw=b"\x00\x01\x02"))
        result = parser.extract_text_for_hermes(req)
        assert result == "[RAW: 3 bytes]"

    # ---- Mixed Parts ----

    def test_mixed_parts(self):
        parser = self._make_parser()
        data_val = struct_pb2.Value(string_value='{"k": "v"}')
        req = _make_request(
            Part(text="See this file: "),
            Part(
                url="http://example.com/doc.pdf",
                filename="doc.pdf",
                media_type="application/pdf",
            ),
            Part(data=data_val, media_type="application/json"),
        )
        result = parser.extract_text_for_hermes(req)
        assert result == (
            'See this file: '
            '[FILE: doc.pdf (application/pdf) - http://example.com/doc.pdf]'
            '[DATA: application/json - {"k": "v"}]'
        )

    # ---- Empty message ----

    def test_empty_parts(self):
        parser = self._make_parser()
        req = _make_request()
        result = parser.extract_text_for_hermes(req)
        assert result == ""

    # ---- Text + Raw mixed ----

    def test_text_and_raw(self):
        parser = self._make_parser()
        req = _make_request(
            Part(text="Binary data: "),
            Part(raw=b"\xff\xfe"),
        )
        result = parser.extract_text_for_hermes(req)
        assert result == "Binary data: [RAW: 2 bytes]"


# ------------------------------------------------------------------
# Integration test: MessageParser wired into HermesA2AHandler
# ------------------------------------------------------------------

class TestMessageParserIntegration:
    """Integration: multi-part messages flow through HermesA2AHandler."""

    @pytest.mark.asyncio
    async def test_handler_uses_message_parser_for_multipart(
        self, mock_hermes_client, task_store
    ):
        """on_message_send should pass parsed multi-part text to hermes client."""
        from hermes_a2a.a2a_handler import HermesA2AHandler
        from a2a.server.context import ServerCallContext

        handler = HermesA2AHandler(mock_hermes_client, task_store)

        data_val = struct_pb2.Value(string_value='{"k": "v"}')
        req = SendMessageRequest(
            message=Message(
                role="ROLE_USER",
                parts=[
                    Part(text="Check this: "),
                    Part(
                        url="http://example.com/f.pdf",
                        filename="f.pdf",
                        media_type="application/pdf",
                    ),
                    Part(data=data_val, media_type="application/json"),
                ],
                context_id="ctx-integ",
            ),
        )

        ctx = ServerCallContext()

        await handler.on_message_send(req, ctx)

        # Verify hermes_client.send_message was called with the combined text
        mock_hermes_client.send_message.assert_called_once()
        call_args = mock_hermes_client.send_message.call_args
        text_arg = call_args[0][0]  # first positional arg
        assert text_arg == (
            'Check this: '
            '[FILE: f.pdf (application/pdf) - http://example.com/f.pdf]'
            '[DATA: application/json - {"k": "v"}]'
        )
