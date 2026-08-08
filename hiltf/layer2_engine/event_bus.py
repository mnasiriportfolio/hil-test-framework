"""Layer 2 — a tiny synchronous publish/subscribe event bus.

Test keywords *publish* results; reporters *subscribe*. Keywords never hold a
private results list and never call a reporter directly. This decoupling is what
lets one run feed many outputs (console, markdown, a merged spreadsheet) without
touching the test logic.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    topic: str
    payload: dict[str, Any] = field(default_factory=dict)


Subscriber = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Subscriber]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Subscriber) -> None:
        self._subs[topic].append(handler)

    def publish(self, topic: str, **payload: Any) -> None:
        event = Event(topic=topic, payload=payload)
        for handler in self._subs.get(topic, []):
            handler(event)
