"""
message_bus.py
Shared in-memory message bus for all agents.
Stores all messages and supports delivery to named agents.
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Optional


class MessageBus:
    def __init__(self):
        # All messages ever sent: list of dicts
        self._all_messages: list[dict] = []
        # Per-agent inbox: agent_name -> list of messages
        self._inboxes: dict[str, list[dict]] = {}

    # ------------------------------------------------------------------
    # Core helpers
    # ------------------------------------------------------------------
    def _ensure_inbox(self, agent: str):
        if agent not in self._inboxes:
            self._inboxes[agent] = []

    def send(
        self,
        from_agent: str,
        to_agent: str,
        message_type: str,
        payload: dict,
        parent_message_id: Optional[str] = None,
    ) -> dict:
        """Build a canonical message, store it, and deliver to recipient's inbox."""
        msg = {
            "message_id": f"msg-{uuid.uuid4().hex[:8]}",
            "from_agent": from_agent,
            "to_agent": to_agent,
            "message_type": message_type,   # task | result | revision_request | confirmation
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if parent_message_id:
            msg["parent_message_id"] = parent_message_id

        self._all_messages.append(msg)
        self._ensure_inbox(to_agent)
        self._inboxes[to_agent].append(msg)

        print(
            f"  [BUS] {from_agent} → {to_agent} | {message_type} | id={msg['message_id']}"
        )
        return msg

    def receive(self, agent: str) -> list[dict]:
        """Return and clear all pending messages for an agent."""
        self._ensure_inbox(agent)
        msgs = list(self._inboxes[agent])
        self._inboxes[agent] = []
        return msgs

    def peek(self, agent: str) -> list[dict]:
        """Return messages without clearing."""
        self._ensure_inbox(agent)
        return list(self._inboxes[agent])

    # ------------------------------------------------------------------
    # Audit / logging
    # ------------------------------------------------------------------
    def history(self) -> list[dict]:
        return list(self._all_messages)

    def history_for(self, agent: str) -> list[dict]:
        return [
            m for m in self._all_messages
            if m["from_agent"] == agent or m["to_agent"] == agent
        ]

    def dump_log(self, path: str = "logs/message_bus.json"):
        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._all_messages, f, indent=2)
        print(f"[BUS] Full message log saved → {path}")


# Singleton used by all agents
bus = MessageBus()
