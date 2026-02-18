"""Parsing utilities for Hikvision ISAPI XML payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
import xml.etree.ElementTree as ET


def local_name(tag: str) -> str:
    """Return tag name without XML namespace."""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def first_text(root: ET.Element, tag_name: str) -> str | None:
    """Find first text value for a tag (namespace agnostic)."""
    for elem in root.iter():
        if local_name(elem.tag) == tag_name:
            text = elem.text.strip() if elem.text else ""
            return text or None
    return None


def parse_event_notification(xml_payload: str) -> dict[str, str | int | None] | None:
    """Parse EventNotificationAlert payload into a dictionary."""
    try:
        root = ET.fromstring(xml_payload)
    except ET.ParseError:
        return None

    if local_name(root.tag) != "EventNotificationAlert":
        return None

    channel_text = first_text(root, "channelID")
    channel_id: int | None = None
    if channel_text is not None:
        try:
            channel_id = int(channel_text)
        except ValueError:
            channel_id = None

    return {
        "event_type": first_text(root, "eventType"),
        "event_state": first_text(root, "eventState"),
        "channel_id": channel_id,
        "target_type": first_text(root, "targetType"),
        "date_time": first_text(root, "dateTime"),
    }


def parse_channel_ids(xml_payload: str) -> list[int]:
    """Parse channel IDs from a discovery response body."""
    try:
        root = ET.fromstring(xml_payload)
    except ET.ParseError:
        return []

    found: set[int] = set()
    for elem in root.iter():
        name = local_name(elem.tag)
        if name not in {"channelID", "id"}:
            continue
        if not elem.text:
            continue
        value = elem.text.strip()
        if not value:
            continue
        try:
            found.add(int(value))
        except ValueError:
            continue

    return sorted(found)


@dataclass(slots=True)
class AlertStreamParser:
    """Incremental parser extracting EventNotificationAlert XML blocks."""

    buffer: str = ""
    _start_token: str = field(init=False, default="<EventNotificationAlert")
    _end_token: str = field(init=False, default="</EventNotificationAlert>")

    def feed(self, chunk: bytes) -> list[str]:
        """Feed raw bytes and return extracted XML documents."""
        self.buffer += chunk.decode("utf-8", errors="ignore")
        docs: list[str] = []

        while True:
            start = self.buffer.find(self._start_token)
            if start < 0:
                if len(self.buffer) > 65536:
                    self.buffer = self.buffer[-32768:]
                break

            end = self.buffer.find(self._end_token, start)
            if end < 0:
                if start > 0:
                    self.buffer = self.buffer[start:]
                break

            end += len(self._end_token)
            docs.append(self.buffer[start:end])
            self.buffer = self.buffer[end:]

        return docs
