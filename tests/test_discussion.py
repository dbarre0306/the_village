import asyncio
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from crewai import Agent

from the_village.discussion import (
    DECLINED_TO_RESPOND,
    INITIAL_BUDGET,
    AddressResolution,
    AdvanceStatus,
    DiscussionFlow,
    DiscussionRunner,
    TurnOutput,
    _active_participants,
    _build_prompt,
    _build_round,
    _format_deaths,
    _format_history,
    _generate_bonus_reply,
    _infer_player_target,
    _last_speaker_today,
    _LegacyTurnOutput,
    _record_message,
    _resolve_target,
    _run_ai_turn,
    _run_player_turn,
    advance,
    start_discussion,
)
from the_village.bridge import FlowStatus, PlayerInput, SessionBridge
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
    assert runner.budgets == {"Dana": 2, "A": 2, "B": 2, "C": 2, "E": 2, "F": 2}


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
    runner = start_discussion(state, random.Random(1))
    # Provide default mocked agents that pass by default (can be overridden in
    # tests). A decline only costs one budget point now, so a participant may
    # be asked again in a later round -- queue up enough declines to cover
    # their whole budget.
    for name in runner.agents:
        runner.agents[name] = ScriptedAgent(
            [_LegacyTurnOutput(has_something_to_say=False)] * INITIAL_BUDGET
        )
    runner.address_resolver = StaticResolver()
    return runner


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
    assert _last_speaker_today(runner.state) is None


def test_last_speaker_today_ignores_other_days():
    runner = make_runner(day_number=2)
    runner.state.discussion.append(
        DiscussionMessage(day_number=1, speaker="A", message="yesterday")
    )
    assert _last_speaker_today(runner.state) is None


def test_last_speaker_today_returns_most_recent_todays_speaker():
    runner = make_runner()
    runner.state.discussion.append(
        DiscussionMessage(day_number=1, speaker="A", message="first")
    )
    runner.state.discussion.append(
        DiscussionMessage(day_number=1, speaker="B", message="second")
    )
    assert _last_speaker_today(runner.state) == "B"


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
    assert _resolve_target(None, runner.state, exclude="A") is None


def test_resolve_target_returns_none_for_self_address():
    runner = make_runner()
    assert _resolve_target("A", runner.state, exclude="A") is None


def test_resolve_target_returns_none_for_unknown_name():
    runner = make_runner()
    assert _resolve_target("Ghost", runner.state, exclude="A") is None


def test_resolve_target_allows_passed_participant():
    # Bonus/direct replies let someone respond out of turn regardless of
    # their round-queue status, so having passed for the day doesn't
    # disqualify them as an addressed_to target.
    runner = make_runner()
    runner.passed.add("B")
    assert _resolve_target("B", runner.state, exclude="A") == "B"


def test_resolve_target_returns_valid_target():
    runner = make_runner()
    assert _resolve_target("B", runner.state, exclude="A") == "B"


def test_infer_player_target_returns_resolver_output():
    runner = make_runner()
    runner.address_resolver = StaticResolver("B")
    assert _infer_player_target(runner, "B, where were you?") == "B"


def test_infer_player_target_returns_none_when_resolver_finds_no_target():
    runner = make_runner()
    runner.address_resolver = StaticResolver(None)
    assert _infer_player_target(runner, "I'm scared.") is None


def test_infer_player_target_skips_resolver_call_when_no_other_participants():
    state = GameState(player_name="Dana", villagers=[Villager(name="Dana", player_type="user")])
    runner = DiscussionRunner(
        state=state, agents={}, budgets={"Dana": 2}, address_resolver=BoomResolver()
    )
    assert _infer_player_target(runner, "Anyone there?") is None


class ScriptedAgent:
    def __init__(self, outputs):
        self._outputs = list(outputs)

    def kickoff(self, messages, response_format=None):
        return SimpleNamespace(pydantic=self._outputs.pop(0))


class StaticResolver:
    """Always resolves the player's addressed-to target to a fixed name."""

    def __init__(self, addressed_to=None):
        self._response = AddressResolution(addressed_to=addressed_to)

    def kickoff(self, messages, response_format=None):
        return SimpleNamespace(pydantic=self._response)


class BoomResolver:
    """Fails the test if the resolver is ever called."""

    def kickoff(self, messages, response_format=None):
        raise AssertionError("address_resolver should not have been called")


def test_format_deaths_with_no_deaths():
    state = GameState(player_name="Dana")
    assert _format_deaths(state) == "(No one has died yet.)"


def test_format_deaths_lists_each_death():
    state = GameState(player_name="Dana", deaths=[Death(name="D", day_number=2)])
    assert _format_deaths(state) == "D was found dead on Monday."


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
    prompt = _build_prompt(state, addressed_by=None, budget=INITIAL_BUDGET)
    assert "It's your turn" in prompt


def test_build_prompt_with_addressed_by_includes_the_question():
    state = GameState(player_name="Dana")
    msg = DiscussionMessage(day_number=1, speaker="A", message="Where were you?")
    prompt = _build_prompt(state, addressed_by=msg, budget=INITIAL_BUDGET)
    assert "A just said to you" in prompt
    assert "Where were you?" in prompt


def test_generate_bonus_reply_records_message_and_costs_no_budget():
    runner = make_runner()
    runner.agents["B"] = ScriptedAgent(
        [_LegacyTurnOutput(has_something_to_say=True, message="I was home.")]
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


def test_generate_bonus_reply_records_decline_placeholder_when_agent_declines():
    runner = make_runner()
    runner.agents["B"] = ScriptedAgent([_LegacyTurnOutput(has_something_to_say=False)])
    asking = DiscussionMessage(
        day_number=1, speaker="A", message="Where were you?", addressed_to="B"
    )

    reply = _generate_bonus_reply(runner, asking)

    assert reply.speaker == "B"
    assert reply.message == DECLINED_TO_RESPOND
    assert reply.addressed_to is None
    assert runner.state.discussion == [reply]


def test_generate_bonus_reply_does_not_chain_further_bonus_replies():
    runner = make_runner()
    runner.agents["B"] = ScriptedAgent(
        [
            _LegacyTurnOutput(
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


def test_advance_auto_plays_ai_turns_then_pauses_for_player():
    runner = make_runner()
    runner.agents["A"] = ScriptedAgent(
        [_LegacyTurnOutput(has_something_to_say=True, message="I'm scared.")]
    )
    runner.agents["B"] = ScriptedAgent(
        [_LegacyTurnOutput(has_something_to_say=True, message="Me too.")]
    )
    runner.queue = ["A", "B", "Dana"]

    events = list(advance(runner))

    messages = [e for e in events if isinstance(e, DiscussionMessage)]
    assert [m.speaker for m in messages] == ["A", "B"]
    assert events[-1] == AdvanceStatus.WAITING_FOR_TURN
    assert runner.queue == ["Dana"]


def test_advance_records_player_message_and_decrements_budget():
    runner = make_runner()
    runner.queue = ["Dana"]
    starting_budget = runner.budgets["Dana"]

    events = list(advance(runner, player_input="I didn't do it!"))

    messages = [e for e in events if isinstance(e, DiscussionMessage)]
    assert messages[0].speaker == "Dana"
    assert messages[0].message == "I didn't do it!"
    assert runner.budgets["Dana"] == starting_budget - 1


def test_advance_player_pass_on_normal_turn_costs_budget_but_gives_another_chance():
    runner = make_runner()
    runner.queue = ["Dana"]
    starting_budget = runner.budgets["Dana"]

    list(advance(runner, player_pass=True))

    assert runner.budgets["Dana"] == starting_budget - 1
    assert "Dana" not in runner.passed


def test_advance_player_pass_is_permanent_once_budget_is_exhausted():
    runner = make_runner()
    runner.budgets["Dana"] = 1
    runner.queue = ["Dana"]

    list(advance(runner, player_pass=True))

    assert runner.budgets["Dana"] == 0
    assert "Dana" in runner.passed


def test_advance_pauses_for_player_when_addressed_by_ai():
    runner = make_runner()
    runner.agents["A"] = ScriptedAgent(
        [
            _LegacyTurnOutput(
                has_something_to_say=True,
                message="Dana, where were you last night?",
                addressed_to="Dana",
            )
        ]
    )
    runner.queue = ["A", "B"]

    events = list(advance(runner))

    assert events[-1] == AdvanceStatus.WAITING_FOR_ANSWER
    assert runner.awaiting_reply_from == "Dana"
    assert runner.queue == ["B"]


def test_advance_player_answer_to_question_costs_no_budget_and_resumes_queue():
    runner = make_runner()
    runner.agents["B"] = ScriptedAgent(
        [_LegacyTurnOutput(has_something_to_say=True, message="I agree with Dana.")]
    )
    runner.awaiting_reply_from = "Dana"
    runner.queue = ["B"]
    starting_budget = runner.budgets["Dana"]

    events = list(advance(runner, player_input="I was home asleep."))

    messages = [e for e in events if isinstance(e, DiscussionMessage)]
    assert messages[0].speaker == "Dana"
    assert messages[0].message == "I was home asleep."
    assert runner.budgets["Dana"] == starting_budget
    assert runner.awaiting_reply_from is None
    assert messages[1].speaker == "B"


def test_advance_player_answer_addressing_someone_new_gets_bonus_reply():
    """The player's message here is itself an answer to a question, but if
    it addresses someone new, that person should get a bonus reply too --
    same as a normal queued turn would trigger.
    """
    runner = make_runner()
    runner.address_resolver = StaticResolver("B")
    runner.agents["B"] = ScriptedAgent(
        [_LegacyTurnOutput(has_something_to_say=True, message="I was home.")]
    )
    runner.awaiting_reply_from = "Dana"
    runner.queue = []

    events = list(advance(runner, player_input="B, where were you?"))

    messages = [e for e in events if isinstance(e, DiscussionMessage)]
    assert [m.speaker for m in messages] == ["Dana", "B"]


def test_advance_player_answer_addressing_someone_who_turns_it_back_pauses_for_answer():
    runner = make_runner()
    runner.address_resolver = StaticResolver("A")
    runner.agents["A"] = ScriptedAgent(
        [
            _LegacyTurnOutput(
                has_something_to_say=True,
                message="You're the only one who'd know.",
                addressed_to="Dana",
            )
        ]
    )
    runner.awaiting_reply_from = "Dana"
    runner.queue = []

    events = list(advance(runner, player_input="A, where were you?"))

    messages = [e for e in events if isinstance(e, DiscussionMessage)]
    assert [m.speaker for m in messages] == ["Dana", "A"]
    assert events[-1] == AdvanceStatus.WAITING_FOR_ANSWER
    assert runner.awaiting_reply_from == "Dana"


def test_advance_player_pass_when_addressed_is_not_permanent():
    runner = make_runner()
    runner.awaiting_reply_from = "Dana"
    runner.queue = []

    list(advance(runner, player_pass=True))

    assert "Dana" not in runner.passed


def test_advance_records_decline_placeholder_when_player_passes_on_a_question():
    runner = make_runner()
    runner.awaiting_reply_from = "Dana"
    runner.budgets = {"Dana": 1, "A": 0, "B": 0, "C": 0, "E": 0, "F": 0}
    runner.queue = []

    events = list(advance(runner, player_pass=True))

    messages = [e for e in events if isinstance(e, DiscussionMessage)]
    assert [m.speaker for m in messages] == ["Dana"]
    assert messages[0].message == DECLINED_TO_RESPOND
    assert messages[0].addressed_to is None
    assert runner.awaiting_reply_from is None


def test_advance_completes_when_no_participants_remain_active():
    runner = make_runner()
    runner.budgets = {"Dana": 0, "A": 0, "B": 0, "C": 0, "E": 0, "F": 0}
    runner.queue = []

    events = list(advance(runner))

    assert events == [AdvanceStatus.COMPLETE]


def test_advance_completes_when_only_one_participant_remains_active_instead_of_repeating():
    runner = make_runner()
    runner.passed.update({"Dana", "B", "C", "E", "F"})
    runner.state.discussion.append(
        DiscussionMessage(day_number=1, speaker="A", message="Only me left.")
    )
    runner.agents["A"] = ScriptedAgent(
        [_LegacyTurnOutput(has_something_to_say=True, message="Should not be asked again.")]
    )
    runner.queue = []

    events = list(advance(runner))

    assert events == [AdvanceStatus.COMPLETE]


def test_advance_defers_sole_remaining_queue_entry_when_others_are_still_active():
    """A bonus reply can leave someone as the only entry left in this round's
    queue while they're also the last speaker. Even though nobody else can
    join *this* round, other participants (A and C here) are still active for
    a future round, so B must not be asked again immediately -- the round
    should end and let a fresh round (which can include everyone) resolve it.
    """
    runner = make_runner()
    runner.passed.update({"Dana", "E", "F"})
    runner.budgets["A"] = 1
    runner.budgets["C"] = 1
    runner.state.discussion.append(
        DiscussionMessage(day_number=1, speaker="B", message="B's bonus reply.")
    )
    runner.agents["B"] = ScriptedAgent(
        [_LegacyTurnOutput(has_something_to_say=True, message="B should not repeat.")]
    )
    runner.agents["A"] = ScriptedAgent([_LegacyTurnOutput(has_something_to_say=False)])
    runner.agents["C"] = ScriptedAgent([_LegacyTurnOutput(has_something_to_say=False)])
    runner.queue = ["B"]

    events = list(advance(runner))

    messages = [e for e in events if isinstance(e, DiscussionMessage)]
    assert messages == []
    assert events[-1] == AdvanceStatus.COMPLETE


def test_advance_ai_addressing_another_ai_does_not_pause_for_player():
    runner = make_runner()
    runner.agents["A"] = ScriptedAgent(
        [
            _LegacyTurnOutput(
                has_something_to_say=True,
                message="B, explain yourself.",
                addressed_to="B",
            )
        ]
    )
    runner.agents["B"] = ScriptedAgent(
        [_LegacyTurnOutput(has_something_to_say=True, message="I have nothing to hide.")]
    )
    runner.queue = ["A", "Dana"]

    events = list(advance(runner))

    messages = [e for e in events if isinstance(e, DiscussionMessage)]
    assert [m.speaker for m in messages] == ["A", "B"]
    assert events[-1] == AdvanceStatus.WAITING_FOR_TURN


def test_advance_bonus_reply_addressing_player_pauses_for_answer():
    runner = make_runner()
    runner.agents["A"] = ScriptedAgent(
        [
            _LegacyTurnOutput(
                has_something_to_say=True,
                message="B, where were you?",
                addressed_to="B",
            )
        ]
    )
    runner.agents["B"] = ScriptedAgent(
        [
            _LegacyTurnOutput(
                has_something_to_say=True,
                message="Why don't you ask Dana instead?",
                addressed_to="Dana",
            )
        ]
    )
    runner.queue = ["A", "Dana"]

    events = list(advance(runner))

    messages = [e for e in events if isinstance(e, DiscussionMessage)]
    assert [m.speaker for m in messages] == ["A", "B"]
    assert events[-1] == AdvanceStatus.WAITING_FOR_ANSWER
    assert runner.awaiting_reply_from == "Dana"
    assert runner.queue == ["Dana"]


def test_advance_defers_players_queued_turn_after_answering_direct_question():
    runner = make_runner()
    runner.agents["A"] = ScriptedAgent(
        [
            _LegacyTurnOutput(
                has_something_to_say=True,
                message="Dana, where were you last night?",
                addressed_to="Dana",
            )
        ]
    )
    runner.agents["B"] = ScriptedAgent(
        [_LegacyTurnOutput(has_something_to_say=True, message="I agree with Dana.")]
    )
    runner.queue = ["A", "Dana", "B"]

    events = list(advance(runner))
    assert events[-1] == AdvanceStatus.WAITING_FOR_ANSWER
    assert runner.queue == ["Dana", "B"]

    events = list(advance(runner, player_input="I was home asleep."))

    messages = [e for e in events if isinstance(e, DiscussionMessage)]
    speakers = [m.speaker for m in messages]
    assert speakers[0] == "Dana"
    assert "B" in speakers, "B should get a turn instead of Dana being re-prompted"
    for prev, nxt in zip(speakers, speakers[1:]):
        assert prev != nxt, f"{prev} spoke twice in a row: {speakers}"


def test_advance_defers_ai_queued_turn_after_bonus_reply():
    runner = make_runner()
    decline = _LegacyTurnOutput(has_something_to_say=False)
    runner.agents["A"] = ScriptedAgent(
        [
            _LegacyTurnOutput(
                has_something_to_say=True,
                message="B, explain yourself.",
                addressed_to="B",
            ),
            decline,
        ]
    )
    runner.agents["B"] = ScriptedAgent(
        [
            _LegacyTurnOutput(has_something_to_say=True, message="I have nothing to hide."),
            _LegacyTurnOutput(has_something_to_say=True, message="Anyway, moving on."),
            decline,
        ]
    )
    runner.agents["C"] = ScriptedAgent(
        [_LegacyTurnOutput(has_something_to_say=True, message="Interesting."), decline]
    )
    runner.queue = ["A", "B", "C"]

    events = list(advance(runner))

    speakers = [e.speaker for e in events if isinstance(e, DiscussionMessage)]
    # A's bonus reply from B is inline and immediate (by design); what this
    # guards against is B's own still-queued turn firing right after it.
    assert speakers[:4] == ["A", "B", "C", "B"]
    for prev, nxt in zip(speakers, speakers[1:]):
        assert prev != nxt, f"{prev} spoke twice in a row: {speakers}"


def test_advance_player_bonus_reply_addressing_player_pauses_for_answer():
    runner = make_runner()
    runner.agents["A"] = ScriptedAgent(
        [
            _LegacyTurnOutput(
                has_something_to_say=True,
                message="You're the only one who'd know.",
                addressed_to="Dana",
            )
        ]
    )
    runner.queue = ["Dana"]
    runner.address_resolver = StaticResolver("A")

    events = list(advance(runner, player_input="A, where were you?"))

    messages = [e for e in events if isinstance(e, DiscussionMessage)]
    assert [m.speaker for m in messages] == ["Dana", "A"]
    assert events[-1] == AdvanceStatus.WAITING_FOR_ANSWER
    assert runner.awaiting_reply_from == "Dana"


def test_advance_records_decline_placeholder_and_completes_when_addressed_ai_declines():
    runner = make_runner()
    runner.address_resolver = StaticResolver("A")
    runner.agents["A"] = ScriptedAgent([_LegacyTurnOutput(has_something_to_say=False)])
    runner.budgets = {"Dana": 1, "A": 1, "B": 0, "C": 0, "E": 0, "F": 0}
    runner.queue = ["Dana"]

    events = list(advance(runner, player_input="A, where were you?"))

    messages = [e for e in events if isinstance(e, DiscussionMessage)]
    assert [m.speaker for m in messages] == ["Dana", "A"]
    assert messages[-1].message == DECLINED_TO_RESPOND
    assert messages[-1].addressed_to is None
    assert events[-1] == AdvanceStatus.COMPLETE


def test_advance_ai_pass_costs_budget_but_gives_another_chance():
    runner = make_runner()
    runner.agents["A"] = ScriptedAgent([_LegacyTurnOutput(has_something_to_say=False)])
    runner.queue = ["A", "Dana"]
    starting_budget = runner.budgets["A"]

    events = list(advance(runner))

    assert [e for e in events if isinstance(e, DiscussionMessage)] == []
    assert runner.budgets["A"] == starting_budget - 1
    assert "A" not in runner.passed
    assert events[-1] == AdvanceStatus.WAITING_FOR_TURN


def test_advance_bonus_reply_addressing_a_third_villager_also_gets_a_bonus_reply():
    """A's real turn addresses B; B's bonus reply redirects to C instead of
    answering. C was just asked a direct question by name and should get her
    own bonus reply -- the discussion shouldn't fall through to the next
    queued turn (e.g. the player) while C's question sits unanswered.
    """
    runner = make_runner()
    runner.agents["A"] = ScriptedAgent(
        [
            _LegacyTurnOutput(
                has_something_to_say=True,
                message="B, where were you?",
                addressed_to="B",
            )
        ]
    )
    runner.agents["B"] = ScriptedAgent(
        [
            _LegacyTurnOutput(
                has_something_to_say=True,
                message="Ask C, not me.",
                addressed_to="C",
            )
        ]
    )
    runner.agents["C"] = ScriptedAgent(
        [_LegacyTurnOutput(has_something_to_say=True, message="Fine, I'll answer.")]
    )
    runner.queue = ["A", "Dana"]

    events = list(advance(runner))

    speakers = [e.speaker for e in events if isinstance(e, DiscussionMessage)]
    assert speakers == ["A", "B", "C"]


def test_advance_bonus_reply_chain_stops_on_repeat_to_avoid_ping_pong():
    """If the chain loops back to someone who already spoke in it (B replies
    to A, who originally asked), stop chaining instead of bonus-replying
    forever.
    """
    runner = make_runner()
    runner.agents["A"] = ScriptedAgent(
        [
            _LegacyTurnOutput(
                has_something_to_say=True,
                message="B, where were you?",
                addressed_to="B",
            ),
            _LegacyTurnOutput(has_something_to_say=False),
        ]
    )
    runner.agents["B"] = ScriptedAgent(
        [
            _LegacyTurnOutput(
                has_something_to_say=True,
                message="Why don't you tell us, A?",
                addressed_to="A",
            )
        ]
    )
    runner.queue = ["A", "Dana"]

    events = list(advance(runner))

    speakers = [e.speaker for e in events if isinstance(e, DiscussionMessage)]
    assert speakers == ["A", "B"]


def test_advance_ai_pass_is_permanent_once_budget_is_exhausted():
    runner = make_runner()
    runner.budgets["A"] = 1
    runner.agents["A"] = ScriptedAgent([_LegacyTurnOutput(has_something_to_say=False)])
    runner.queue = ["A", "Dana"]

    events = list(advance(runner))

    assert [e for e in events if isinstance(e, DiscussionMessage)] == []
    assert runner.budgets["A"] == 0
    assert "A" in runner.passed
    assert events[-1] == AdvanceStatus.WAITING_FOR_TURN


def make_discussion_state() -> GameState:
    villagers = [
        Villager(name="Dana", player_type="user"),
        Villager(name="A", player_type="villager"),
        Villager(name="B", player_type="villager"),
    ]
    return GameState(player_name="Dana", day_number=1, villagers=villagers)


def _crew_result(*pydantic_outputs):
    return SimpleNamespace(
        tasks_output=[SimpleNamespace(pydantic=p) for p in pydantic_outputs]
    )


def _stub_agent() -> Agent:
    """A minimal real Agent -- Task/Crew construction validates that `agent`
    fields are actual Agent instances, so a plain object() won't do, even
    though Crew.akickoff is mocked in these tests."""
    return Agent(role="Stub", goal="stub", backstory="stub")


def test_turn_output_has_no_addressed_to_field():
    assert "addressed_to" not in TurnOutput.model_fields


def test_record_message_appends_to_state_discussion_and_returns_it():
    state = make_discussion_state()
    msg = _record_message(state, "A", "hello", addressed_to="B")
    assert state.discussion == [msg]
    assert msg.speaker == "A"
    assert msg.message == "hello"
    assert msg.addressed_to == "B"
    assert msg.day_number == 1


def test_resolve_target_rejects_self_and_unknown_names():
    state = make_discussion_state()
    assert _resolve_target(None, state, exclude="A") is None
    assert _resolve_target("A", state, exclude="A") is None
    assert _resolve_target("Ghost", state, exclude="A") is None
    assert _resolve_target("B", state, exclude="A") == "B"


async def test_run_ai_turn_returns_none_on_scheduled_decline():
    state = make_discussion_state()
    with patch(
        "the_village.discussion.Crew.akickoff",
        new=AsyncMock(return_value=_crew_result(TurnOutput(has_something_to_say=False), None)),
    ):
        message = await _run_ai_turn(
            speaker=_stub_agent(), analyst=_stub_agent(), state=state, name="A", addressed_by=None
        )
    assert message is None
    assert state.discussion == []


async def test_run_ai_turn_records_decline_placeholder_when_owed_a_reply():
    state = make_discussion_state()
    asking = _record_message(state, "B", "Where were you?", addressed_to="A")
    with patch(
        "the_village.discussion.Crew.akickoff",
        new=AsyncMock(return_value=_crew_result(TurnOutput(has_something_to_say=False), None)),
    ):
        message = await _run_ai_turn(
            speaker=_stub_agent(), analyst=_stub_agent(), state=state, name="A", addressed_by=asking
        )
    assert message.speaker == "A"
    assert message.message == DECLINED_TO_RESPOND
    assert message.addressed_to is None


async def test_run_ai_turn_records_message_and_resolved_address():
    state = make_discussion_state()
    with patch(
        "the_village.discussion.Crew.akickoff",
        new=AsyncMock(
            return_value=_crew_result(
                TurnOutput(has_something_to_say=True, message="I saw B leave."),
                AddressResolution(addressed_to="B"),
            )
        ),
    ):
        message = await _run_ai_turn(
            speaker=_stub_agent(), analyst=_stub_agent(), state=state, name="A", addressed_by=None
        )
    assert message.message == "I saw B leave."
    assert message.addressed_to == "B"
    assert state.discussion == [message]


async def test_run_ai_turn_discards_addressed_to_from_the_analyst_on_decline():
    """Even if the analyst task somehow returns an address for a decline (it
    still runs -- see the spec's resolved decision to keep one uniform
    two-task Crew shape), a decline's recorded message must not carry it."""
    state = make_discussion_state()
    with patch(
        "the_village.discussion.Crew.akickoff",
        new=AsyncMock(
            return_value=_crew_result(
                TurnOutput(has_something_to_say=False), AddressResolution(addressed_to="B")
            )
        ),
    ):
        message = await _run_ai_turn(
            speaker=_stub_agent(), analyst=_stub_agent(), state=state, name="A", addressed_by=None
        )
    assert message is None


async def test_run_player_turn_returns_none_on_scheduled_pass():
    # SessionBridge.resolve_input() is a no-op until wait_for_input() has a
    # pending future (see test_bridge.py), so the task must reach its await
    # point (a sleep(0) yield) before we resolve it -- calling resolve_input
    # first would hang forever.
    state = make_discussion_state()
    bridge = SessionBridge()
    task = asyncio.create_task(
        _run_player_turn(
            analyst=_stub_agent(), state=state, bridge=bridge, name="Dana", addressed_by=None
        )
    )
    await asyncio.sleep(0)
    bridge.resolve_input(PlayerInput(message=None))
    message = await task
    assert message is None
    assert state.discussion == []


async def test_run_player_turn_records_decline_placeholder_when_owed_a_reply():
    state = make_discussion_state()
    asking = _record_message(state, "A", "Where were you?", addressed_to="Dana")
    bridge = SessionBridge()
    task = asyncio.create_task(
        _run_player_turn(
            analyst=_stub_agent(), state=state, bridge=bridge, name="Dana", addressed_by=asking
        )
    )
    await asyncio.sleep(0)
    bridge.resolve_input(PlayerInput(message=None))
    message = await task
    assert message.message == DECLINED_TO_RESPOND
    assert message.addressed_to is None


async def test_run_player_turn_resolves_address_via_the_analyst():
    state = make_discussion_state()
    bridge = SessionBridge()
    with patch(
        "the_village.discussion.Crew.akickoff",
        new=AsyncMock(return_value=_crew_result(AddressResolution(addressed_to="B"))),
    ):
        task = asyncio.create_task(
            _run_player_turn(
                analyst=_stub_agent(), state=state, bridge=bridge, name="Dana", addressed_by=None
            )
        )
        await asyncio.sleep(0)
        bridge.resolve_input(PlayerInput(message="B, where were you?"))
        message = await task
    assert message.speaker == "Dana"
    assert message.message == "B, where were you?"
    assert message.addressed_to == "B"


def make_discussion_flow_state() -> GameState:
    villagers = [
        Villager(name="Dana", player_type="user"),
        Villager(name="A", player_type="villager"),
        Villager(name="B", player_type="villager"),
        Villager(name="C", player_type="werewolf", is_pack_leader=True),
        Villager(name="D", player_type="werewolf"),
    ]
    return GameState(player_name="Dana", day_number=1, villagers=villagers)


def _decline_result():
    return _crew_result(TurnOutput(has_something_to_say=False), None)


async def test_discussion_flow_runs_two_rounds_where_everyone_gets_a_turn():
    bridge = SessionBridge()

    async def auto_pass(*_args, **_kwargs):
        return PlayerInput(message=None)

    with (
        patch("the_village.discussion.Crew.akickoff", new=AsyncMock(return_value=_decline_result())),
        patch.object(SessionBridge, "wait_for_input", auto_pass),
    ):
        flow = DiscussionFlow(bridge=bridge, rng=random.Random(1))
        transcript = await flow.kickoff_async(inputs=make_discussion_flow_state().model_dump())

    # Everyone declines every turn in this test, so no DiscussionMessages are
    # recorded -- what's under test is that the flow runs to completion
    # (doesn't hang) across two full rounds without error.
    assert transcript == []


async def test_discussion_flow_pushes_agents_onto_the_bridge():
    bridge = SessionBridge()

    async def auto_pass(*_args, **_kwargs):
        return PlayerInput(message=None)

    with (
        patch("the_village.discussion.Crew.akickoff", new=AsyncMock(return_value=_decline_result())),
        patch.object(SessionBridge, "wait_for_input", auto_pass),
    ):
        flow = DiscussionFlow(bridge=bridge, rng=random.Random(1))
        await flow.kickoff_async(inputs=make_discussion_flow_state().model_dump())

    assert set(bridge.agents.keys()) == {"A", "B", "C", "D"}


class NoShuffleRandom:
    """A `random.Random` stand-in whose `shuffle` is a no-op, so a round's
    order is exactly `_living_participant_names`' insertion order -- lets a
    test assert on *which* participant produces which scripted response
    without depending on a real shuffle's output for a given seed."""

    def shuffle(self, _seq):
        pass

    def randrange(self, start, _stop):
        return start


async def test_discussion_flow_resolves_a_bonus_reply_chain():
    bridge = SessionBridge()
    # make_discussion_flow_state()'s villagers list is [Dana, A, B, C, D];
    # with no shuffling, round order is exactly that -- Dana first (the
    # player, auto-passes below), then A, whose scripted response addresses
    # B; B's own scripted decline stops the chain there.
    state = make_discussion_flow_state()

    speak_and_address_b = _crew_result(
        TurnOutput(has_something_to_say=True, message="B, where were you?"),
        AddressResolution(addressed_to="B"),
    )
    responses = iter([speak_and_address_b] + [_decline_result()] * 20)

    async def scripted_akickoff(*_args, **_kwargs):
        return next(responses)

    async def auto_pass(*_args, **_kwargs):
        return PlayerInput(message=None)

    with (
        patch("the_village.discussion.Crew.akickoff", new=scripted_akickoff),
        patch.object(SessionBridge, "wait_for_input", auto_pass),
    ):
        flow = DiscussionFlow(bridge=bridge, rng=NoShuffleRandom())
        transcript = await flow.kickoff_async(inputs=state.model_dump())

    addressed_messages = [m for m in transcript if m.message == "B, where were you?"]
    assert len(addressed_messages) == 1
    assert addressed_messages[0].speaker == "A"
    assert addressed_messages[0].addressed_to == "B"
    decline_replies = [
        m for m in transcript if m.speaker == "B" and m.message == DECLINED_TO_RESPOND
    ]
    assert len(decline_replies) == 1


async def test_discussion_flow_pauses_for_player_and_resumes():
    bridge = SessionBridge()
    state = make_discussion_flow_state()

    async def scripted_akickoff(*_args, **_kwargs):
        return _decline_result()

    with patch("the_village.discussion.Crew.akickoff", new=scripted_akickoff):
        flow = DiscussionFlow(bridge=bridge, rng=random.Random(1))
        task = asyncio.create_task(flow.kickoff_async(inputs=state.model_dump()))

        # Drain the outbox for the whole run, answering every player-turn
        # pause immediately. The flow asks the player once per round (two
        # rounds total), so more than one pause is expected here -- race
        # each outbox.get() against the flow task itself so a final
        # completion with nothing left in the outbox doesn't leave this
        # loop blocked on a get() that will never resolve.
        seen_waiting_for_turn = False
        while not task.done():
            get_item = asyncio.ensure_future(bridge.outbox.get())
            done, _pending = await asyncio.wait(
                {task, get_item}, return_when=asyncio.FIRST_COMPLETED
            )
            if get_item not in done:
                get_item.cancel()
                break
            item = get_item.result()
            if item == FlowStatus.WAITING_FOR_TURN:
                seen_waiting_for_turn = True
                bridge.resolve_input(PlayerInput(message=None))
        assert seen_waiting_for_turn

        transcript = await task

    assert isinstance(transcript, list)
