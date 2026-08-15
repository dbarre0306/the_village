import random

from the_village.discussion import (
    DiscussionRunner,
    _active_participants,
    _build_round,
    _last_speaker_today,
    start_discussion,
)
from the_village.state import DiscussionMessage, GameState, Villager


def make_state(day_number: int = 2) -> GameState:
    villagers = [
        Villager(name="Dana", player_type="user"),
        Villager(name="A", player_type="villager"),
        Villager(name="B", player_type="villager"),
        Villager(name="C", player_type="villager"),
        Villager(name="D", player_type="villager", is_alive=False),
        Villager(name="E", player_type="werewolf", is_pack_leader=True),
        Villager(name="F", player_type="werewolf"),
    ]
    return GameState(player_name="Dana", day_number=day_number, villagers=villagers)


def test_start_discussion_builds_agent_for_each_living_ai_villager():
    runner = start_discussion(make_state(), random.Random(1))
    assert set(runner.agents.keys()) == {"A", "B", "C", "E", "F"}


def test_start_discussion_excludes_dead_villager():
    runner = start_discussion(make_state(), random.Random(1))
    assert "D" not in runner.agents
    assert "D" not in runner.budgets


def test_start_discussion_budgets_include_player_and_all_living_ai():
    runner = start_discussion(make_state(), random.Random(1))
    assert runner.budgets == {"Dana": 3, "A": 3, "B": 3, "C": 3, "E": 3, "F": 3}


def test_start_discussion_starts_with_empty_queue_and_no_pending_reply():
    runner = start_discussion(make_state(), random.Random(1))
    assert runner.queue == []
    assert runner.awaiting_reply_from is None
    assert runner.passed == set()


def test_start_discussion_returns_discussion_runner_referencing_the_state():
    state = make_state()
    runner = start_discussion(state, random.Random(1))
    assert isinstance(runner, DiscussionRunner)
    assert runner.state is state


def make_runner(day_number: int = 1) -> DiscussionRunner:
    villagers = [
        Villager(name="Dana", player_type="user"),
        Villager(name="A", player_type="villager"),
        Villager(name="B", player_type="villager"),
        Villager(name="C", player_type="villager"),
        Villager(name="E", player_type="werewolf"),
        Villager(name="F", player_type="werewolf"),
    ]
    state = GameState(player_name="Dana", day_number=day_number, villagers=villagers)
    return start_discussion(state, random.Random(1))


def test_active_participants_excludes_passed():
    runner = make_runner()
    runner.passed.add("A")
    assert "A" not in _active_participants(runner)
    assert "B" in _active_participants(runner)


def test_active_participants_excludes_exhausted_budget():
    runner = make_runner()
    runner.budgets["B"] = 0
    assert "B" not in _active_participants(runner)


def test_last_speaker_today_returns_none_with_no_messages():
    runner = make_runner()
    assert _last_speaker_today(runner) is None


def test_last_speaker_today_ignores_other_days():
    runner = make_runner(day_number=2)
    runner.state.discussion.append(
        DiscussionMessage(day_number=1, speaker="A", message="yesterday")
    )
    assert _last_speaker_today(runner) is None


def test_last_speaker_today_returns_most_recent_todays_speaker():
    runner = make_runner()
    runner.state.discussion.append(
        DiscussionMessage(day_number=1, speaker="A", message="first")
    )
    runner.state.discussion.append(
        DiscussionMessage(day_number=1, speaker="B", message="second")
    )
    assert _last_speaker_today(runner) == "B"


def test_build_round_includes_only_active_participants():
    runner = make_runner()
    runner.passed.add("A")
    order = _build_round(runner)
    assert set(order) == {"Dana", "B", "C", "E", "F"}


def test_build_round_never_starts_with_previous_last_speaker():
    runner = make_runner()
    runner.state.discussion.append(
        DiscussionMessage(day_number=1, speaker="C", message="I saw something")
    )
    for seed in range(200):
        runner.rng = random.Random(seed)
        order = _build_round(runner)
        assert order[0] != "C"


def test_build_round_allows_repeat_when_only_one_active_participant():
    runner = make_runner()
    for name in ("Dana", "B", "C", "E", "F"):
        runner.passed.add(name)
    runner.state.discussion.append(
        DiscussionMessage(day_number=1, speaker="A", message="only one left")
    )
    order = _build_round(runner)
    assert order == ["A"]
