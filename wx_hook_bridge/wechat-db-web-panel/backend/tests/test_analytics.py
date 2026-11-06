from backend.app.analytics import (
    bidirectional_ratio,
    count_turns,
    relationship_strength,
    response_durations,
    split_episodes,
)


def test_episode_boundary_and_turns() -> None:
    messages = [
        {"time": 0, "sender": "a", "is_self": False},
        {"time": 1800, "sender": "a", "is_self": False},
        {"time": 3601, "sender": "b", "is_self": True},
        {"time": 3602, "sender": "b", "is_self": True},
    ]
    episodes = split_episodes(messages, 1800)
    assert [len(item) for item in episodes] == [2, 2]
    assert count_turns(episodes[0]) == 1
    assert count_turns(episodes[1]) == 1


def test_response_pairs_latest_incoming_and_applies_offline_threshold() -> None:
    messages = [
        {"time": 10, "sender": "other", "is_self": False},
        {"time": 20, "sender": "other", "is_self": False},
        {"time": 50, "sender": "self", "is_self": True},
        {"time": 100, "sender": "other", "is_self": False},
        {"time": 1000, "sender": "self", "is_self": True},
    ]
    assert response_durations(messages, max_seconds=300) == [30]


def test_relationship_strength_is_bounded_and_monotonic() -> None:
    weak = relationship_strength(
        10,
        2,
        100,
        10,
        0,
        max_messages=100,
        max_active_days=20,
        latest_time=1000,
    )
    strong = relationship_strength(
        100,
        20,
        1000,
        50,
        50,
        max_messages=100,
        max_active_days=20,
        latest_time=1000,
    )
    assert 0 <= weak < strong <= 100
    assert bidirectional_ratio(50, 50) == 1
    assert bidirectional_ratio(10, 0) == 0
