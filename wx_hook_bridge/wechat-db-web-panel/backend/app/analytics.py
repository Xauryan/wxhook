from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


def bidirectional_ratio(sent_count: int, received_count: int) -> float:
    total = sent_count + received_count
    return 2 * min(sent_count, received_count) / total if total else 0.0


def relationship_strength(
    message_count: int,
    active_days: int,
    last_time: int,
    sent_count: int,
    received_count: int,
    *,
    max_messages: int,
    max_active_days: int,
    latest_time: int,
) -> float:
    message_norm = math.log1p(message_count) / math.log1p(max(max_messages, 1))
    active_norm = math.log1p(active_days) / math.log1p(max(max_active_days, 1))
    age_days = max(0.0, (latest_time - last_time) / 86400)
    recency = math.exp(-age_days / 180)
    balance = bidirectional_ratio(sent_count, received_count)
    return round(
        100
        * (
            0.35 * message_norm
            + 0.25 * active_norm
            + 0.20 * recency
            + 0.20 * balance
        ),
        2,
    )


def response_durations(
    messages: Sequence[Mapping[str, Any]], max_seconds: int
) -> list[int]:
    results: list[int] = []
    pending_incoming: int | None = None
    for item in messages:
        timestamp = int(item["time"])
        if item["is_self"]:
            if pending_incoming is not None:
                delta = timestamp - pending_incoming
                if 0 <= delta <= max_seconds:
                    results.append(delta)
                pending_incoming = None
        else:
            pending_incoming = timestamp
    return results


def split_episodes(
    messages: Sequence[Mapping[str, Any]], gap_seconds: int
) -> list[list[Mapping[str, Any]]]:
    episodes: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    for item in messages:
        if current and int(item["time"]) - int(current[-1]["time"]) > gap_seconds:
            episodes.append(current)
            current = []
        current.append(item)
    if current:
        episodes.append(current)
    return episodes


def count_turns(messages: Sequence[Mapping[str, Any]]) -> int:
    if not messages:
        return 0
    return 1 + sum(
        previous["sender"] != item["sender"]
        for previous, item in zip(messages, messages[1:])
    )
