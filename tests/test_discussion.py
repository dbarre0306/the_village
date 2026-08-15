import random
from types import SimpleNamespace

from the_village.discussion import (
    DiscussionRunner,
    TurnOutput,
    _active_participants,
    _build_prompt,
    _build_round,
    _format_deaths,
    _format_history,
    _generate_bonus_reply,
    _last_speaker_today,
    _resolve_target,
    start_discussion,
)
from the_village.state import Death, DiscussionMessage, GameState, Villager


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


def test_resolve_target_returns_none_for_none_candidate():
    runner = make_runner()
    assert _resolve_target(None, runner, exclude="A") is None


def test_resolve_target_returns_none_for_self_address():
    runner = make_runner()
    assert _resolve_target("A", runner, exclude="A") is None


def test_resolve_target_returns_none_for_unknown_name():
    runner = make_runner()
    assert _resolve_target("Ghost", runner, exclude="A") is None


def test_resolve_target_returns_none_for_passed_participant():
    runner = make_runner()
    runner.passed.add("B")
    assert _resolve_target("B", runner, exclude="A") is None


def test_resolve_target_returns_valid_target():
    runner = make_runner()
    assert _resolve_target("B", runner, exclude="A") == "B"


class ScriptedAgent:
    def __init__(self, outputs):
        self._outputs = list(outputs)

    def kickoff(self, messages, response_format=None):
        return SimpleNamespace(pydantic=self._outputs.pop(0))


def test_format_deaths_with_no_deaths():
    state = GameState(player_name="Dana")
    assert _format_deaths(state) == "(No one has died yet.)"


def test_format_deaths_lists_each_death():
    state = GameState(player_name="Dana", deaths=[Death(name="D", day_number=2)])
    assert _format_deaths(state) == "D was found dead on day 2."


def test_format_history_with_no_messages():
    state = GameState(player_name="Dana")
    assert _format_history(state) == "(No discussion has happened yet.)"


def test_format_history_includes_prior_days_in_order():
    state = GameState(
        player_name="Dana",
        discussion=[
            DiscussionMessage(day_number=1, speaker="A", message="yesterday's message"),
            DiscussionMessage(day_number=2, speaker="B", message="today's message"),
        ],
    )
    assert (
        _format_history(state)
        == "A: yesterday's message\nB: today's message"
    )


def test_build_prompt_without_addressed_by_prompts_free_turn():
    state = GameState(player_name="Dana")
    prompt = _build_prompt(state, addressed_by=None)
    assert "It's your turn" in prompt


def test_build_prompt_with_addressed_by_includes_the_question():
    state = GameState(player_name="Dana")
    msg = DiscussionMessage(day_number=1, speaker="A", message="Where were you?")
    prompt = _build_prompt(state, addressed_by=msg)
    assert "A just said to you" in prompt
    assert "Where were you?" in prompt


def test_generate_bonus_reply_records_message_and_costs_no_budget():
    runner = make_runner()
    runner.agents["B"] = ScriptedAgent(
        [TurnOutput(has_something_to_say=True, message="I was home.")]
    )
    original_budget = runner.budgets["B"]
    asking = DiscussionMessage(
        day_number=1, speaker="A", message="Where were you?", addressed_to="B"
    )

    reply = _generate_bonus_reply(runner, asking)

    assert reply.speaker == "B"
    assert reply.message == "I was home."
    assert runner.budgets["B"] == original_budget
    assert runner.state.discussion[-1] == reply


def test_generate_bonus_reply_returns_none_when_agent_declines():
    runner = make_runner()
    runner.agents["B"] = ScriptedAgent([TurnOutput(has_something_to_say=False)])
    asking = DiscussionMessage(
        day_number=1, speaker="A", message="Where were you?", addressed_to="B"
    )

    reply = _generate_bonus_reply(runner, asking)

    assert reply is None
    assert runner.state.discussion == []


def test_generate_bonus_reply_does_not_chain_further_bonus_replies():
    runner = make_runner()
    runner.agents["B"] = ScriptedAgent(
        [
            TurnOutput(
                has_something_to_say=True,
                message="What about you, A?",
                addressed_to="A",
            )
        ]
    )
    asking = DiscussionMessage(
        day_number=1, speaker="A", message="Where were you?", addressed_to="B"
    )

    reply = _generate_bonus_reply(runner, asking)

    assert reply.addressed_to == "A"
    assert len(runner.state.discussion) == 1
