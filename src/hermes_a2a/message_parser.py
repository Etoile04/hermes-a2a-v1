"""Multi-part message parsing for A2A protocol."""

from __future__ import annotations

import logging

from a2a.types.a2a_pb2 import Part, SendMessageRequest

logger = logging.getLogger(__name__)


class MessageParser:
    """Parses A2A multi-part messages and converts them to Hermes-compatible text.

    A2A Part has a oneof `content` field with variants:
        text  (string)        → used directly
        url   (string)        → [FILE: filename (media_type) - url]
        data  (struct Value)  → [DATA: media_type - string_value]
        raw   (bytes)         → [RAW: N bytes]
    """

    def extract_text_for_hermes(self, params: SendMessageRequest) -> str:
        """Extract all parts from message and convert to text for Hermes API.

        Text parts: used directly
        URL parts:  [FILE: filename (media_type) - url]
        Data parts: [DATA: media_type - content]
        Raw parts:  [RAW: N bytes]
        """
        parts = list(params.message.parts)
        text_segments: list[str] = []

        for part in parts:
            kind = part.WhichOneof("content")

            if kind == "text":
                text_segments.append(part.text)

            elif kind == "url":
                filename = part.filename or "unknown"
                media_type = part.media_type or "unknown"
                text_segments.append(
                    f"[FILE: {filename} ({media_type}) - {part.url}]"
                )

            elif kind == "data":
                media_type = part.media_type or "binary"
                # data is a google.protobuf.struct_pb2.Value
                # Extract its string representation
                data_val = part.data
                val_kind = data_val.WhichOneof("kind")
                if val_kind == "string_value":
                    data_str = data_val.string_value
                elif val_kind == "number_value":
                    data_str = str(data_val.number_value)
                elif val_kind == "bool_value":
                    data_str = str(data_val.bool_value)
                elif val_kind == "struct_value":
                    # Serialize struct fields
                    data_str = str(dict(data_val.struct_value.fields))
                elif val_kind == "list_value":
                    data_str = str([v for v in data_val.list_value.values])
                elif val_kind == "null_value":
                    data_str = "<null>"
                else:
                    data_str = ""
                text_segments.append(f"[DATA: {media_type} - {data_str}]")

            elif kind == "raw":
                text_segments.append(f"[RAW: {len(part.raw)} bytes]")

            # else: unknown content kind — skip silently

        return "".join(text_segments)
