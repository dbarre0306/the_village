from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from crewai import Agent

from the_village.bridge import SessionBridge
from the_village.discussion.ai_speaker import (
    _AiSpeaker,
    _build_dead_player_guardrail,
    _reject_turn_order_commentary,
    _reject_unfounded_behavior_claims,
    _SpeakerOutput,
)
from the_village.discussion.speaker import DECLINED_TO_RESPOND, _AddressResolution
from the_village.state import GameState, Player


def make_discussion_state() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
    ]
    return GameState(user_player_name="Dana", players=players)


def _stub_agent() -> Agent:
    """A minimal real Agent -- Task/Crew construction validates that `agent`
    fields are actual Agent instances, so a plain object() won't do, even
    though Crew.akickoff is mocked in these tests."""
    return Agent(role="Stub", goal="stub", backstory="stub")


def _crew_result(*pydantic_outputs):
    return SimpleNamespace(
        tasks_output=[SimpleNamespace(pydantic=p) for p in pydantic_outputs]
    )


def make_ai_speaker(state: GameState, player_name: str = "A") -> _AiSpeaker:
    return _AiSpeaker(
        state, SessionBridge(), player_name, _stub_agent(), _stub_agent()
    )


def test_speaker_output_has_no_addressed_to_field():
    assert "addressed_to" not in _SpeakerOutput.model_fields


def test_guardrail_rejects_comment_about_who_hasnt_spoken():
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(
            has_something_to_say=True,
            text="It's strange that we haven't heard from Don yet.",
        )
    )
    passed, result = _reject_turn_order_commentary(output)
    assert passed is False
    assert "turn order" in result.lower()


def test_guardrail_accepts_ordinary_speech():
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(
            has_something_to_say=True,
            text="I saw B leave the tavern late last night.",
        )
    )
    passed, result = _reject_turn_order_commentary(output)
    assert passed is True
    assert result is output


def test_guardrail_accepts_decline():
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(has_something_to_say=False, text=None)
    )
    passed, result = _reject_turn_order_commentary(output)
    assert passed is True
    assert result is output


def test_speak_prompt_forbids_unfounded_behavior_claims():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert 'seems focused on' in prompt
    assert "been acting odd/strange/suspicious" in prompt
    assert "never comment on who has or hasn't spoken yet" in prompt


def test_speak_prompt_forbids_treating_dead_players_as_active_suspects():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "out of the game" in prompt
    assert "press" in prompt


def test_speak_prompt_forbids_comparing_dead_players_credibility_to_living():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "credibility is still in question" in prompt


def test_speak_prompt_forbids_saying_dead_players_are_evading_questions():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "evading, dodging, or avoiding questions" in prompt


def test_speak_prompt_allows_discussing_the_dead_players_killing():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "still fine to discuss why or how a dead player died" in prompt


def test_unfounded_behavior_guardrail_rejects_acting_odd_claim():
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(
            has_something_to_say=True,
            text=(
                "I can't help but think Lisa's been acting a bit odd lately. "
                "Maybe we should look closer at her movements leading up to "
                "Alice's murder."
            ),
        )
    )
    passed, result = _reject_unfounded_behavior_claims(output)
    assert passed is False
    assert "behaving" in result.lower()


def test_unfounded_behavior_guardrail_rejects_seems_focused_on():
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(
            has_something_to_say=True,
            text="B seems really focused on deflecting attention from himself.",
        )
    )
    passed, result = _reject_unfounded_behavior_claims(output)
    assert passed is False


def test_unfounded_behavior_guardrail_rejects_keeps_bringing_up():
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(
            has_something_to_say=True,
            text="B keeps bringing up Don, which feels intentional.",
        )
    )
    passed, result = _reject_unfounded_behavior_claims(output)
    assert passed is False


def test_unfounded_behavior_guardrail_accepts_ordinary_speech():
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(
            has_something_to_say=True,
            text="I saw B leave the tavern late last night.",
        )
    )
    passed, result = _reject_unfounded_behavior_claims(output)
    assert passed is True
    assert result is output


def test_unfounded_behavior_guardrail_accepts_decline():
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(has_something_to_say=False, text=None)
    )
    passed, result = _reject_unfounded_behavior_claims(output)
    assert passed is True
    assert result is output


def test_guardrail_pipeline_rejects_unfounded_behavior_claims():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    guardrail = speaker._build_guardrail()
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(
            has_something_to_say=True,
            text="B has been acting really suspicious lately.",
        )
    )
    passed, result = guardrail(output)
    assert passed is False


def test_dead_player_guardrail_rejects_whereabouts_pressure():
    guardrail = _build_dead_player_guardrail(["Joshua"])
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(
            has_something_to_say=True,
            text=(
                "It's concerning that Joshua is directing so much suspicion "
                "toward Don while avoiding questions about his own "
                "whereabouts today."
            ),
        )
    )
    passed, result = guardrail(output)
    assert passed is False
    assert "Joshua" in result
    assert "dead" in result.lower()


def test_dead_player_guardrail_rejects_where_was_phrasing():
    guardrail = _build_dead_player_guardrail(["Joshua"])
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(
            has_something_to_say=True,
            text="We need to look into where Joshua was today.",
        )
    )
    passed, result = guardrail(output)
    assert passed is False


def test_dead_player_guardrail_rejects_evading_questions_phrasing():
    guardrail = _build_dead_player_guardrail(["Don"])
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(
            has_something_to_say=True,
            text=(
                "We need to focus on why Don is evading questions while "
                "making accusations."
            ),
        )
    )
    passed, result = guardrail(output)
    assert passed is False
    assert "Don" in result


def test_dead_player_guardrail_accepts_discussion_of_the_killing_itself():
    guardrail = _build_dead_player_guardrail(["Bruce"])
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(
            has_something_to_say=True,
            text=(
                "I was home alone reading last night, but that doesn't "
                "explain why Bruce was killed. We need to think about who "
                "might have had the opportunity to do this; can anyone "
                "account for their whereabouts?"
            ),
        )
    )
    passed, result = guardrail(output)
    assert passed is True
    assert result is output


def test_dead_player_guardrail_accepts_historical_mention():
    guardrail = _build_dead_player_guardrail(["Joshua"])
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(
            has_something_to_say=True,
            text="Joshua was killed by the werewolves last night.",
        )
    )
    passed, result = guardrail(output)
    assert passed is True
    assert result is output


def test_dead_player_guardrail_ignores_living_players():
    guardrail = _build_dead_player_guardrail([])
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(
            has_something_to_say=True,
            text="Where was Don last night? He needs to explain himself.",
        )
    )
    passed, result = guardrail(output)
    assert passed is True
    assert result is output


async def test_returns_none_on_scheduled_decline():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    with patch(
        "crewai.Crew.akickoff",
        new=AsyncMock(
            return_value=_crew_result(_SpeakerOutput(has_something_to_say=False), None)
        ),
    ):
        message = await speaker.speak(addressed_by=None)
    assert message is None
    assert state.current_day.discussion == []


async def test_records_decline_placeholder_when_owed_a_reply():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    asking = speaker._record_message("Where were you?", addressed_to="A")
    with patch(
        "crewai.Crew.akickoff",
        new=AsyncMock(
            return_value=_crew_result(_SpeakerOutput(has_something_to_say=False), None)
        ),
    ):
        message = await speaker.speak(addressed_by=asking)
    assert message.player_name == "A"
    assert message.text == DECLINED_TO_RESPOND
    assert message.addressed_to is None


async def test_records_message_and_resolved_address():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    with patch(
        "crewai.Crew.akickoff",
        new=AsyncMock(
            return_value=_crew_result(
                _SpeakerOutput(has_something_to_say=True, text="I saw B leave."),
                _AddressResolution(addressed_to="B"),
            )
        ),
    ):
        message = await speaker.speak(addressed_by=None)
    assert message.text == "I saw B leave."
    assert message.addressed_to == "B"
    assert state.current_day.discussion == [message]


def test_guardrail_logs_rejection(caplog):
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    guardrail = speaker._build_guardrail()
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(
            has_something_to_say=True,
            text="It's strange that we haven't heard from Don yet.",
        )
    )
    with caplog.at_level("WARNING"):
        passed, result = guardrail(output)
    assert passed is False
    assert "A" in caplog.text
    assert "guardrail" in caplog.text.lower()
    assert result in caplog.text
    assert "It's strange that we haven't heard from Don yet." in caplog.text


def test_guardrail_does_not_log_on_pass(caplog):
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    guardrail = speaker._build_guardrail()
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(
            has_something_to_say=True,
            text="I saw B leave the tavern late last night.",
        )
    )
    with caplog.at_level("WARNING"):
        passed, result = guardrail(output)
    assert passed is True
    assert caplog.text == ""


async def test_returns_none_when_guardrail_keeps_failing_and_no_reply_owed():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    with patch(
        "crewai.Crew.akickoff",
        new=AsyncMock(
            side_effect=Exception(
                "Task failed guardrail validation after 3 retries. Last "
                "error: Bruce is dead and out of the game."
            )
        ),
    ):
        message = await speaker.speak(addressed_by=None)
    assert message is None
    assert state.current_day.discussion == []


async def test_records_decline_placeholder_when_guardrail_keeps_failing_and_reply_owed():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    asking = speaker._record_message("Where were you?", addressed_to="A")
    with patch(
        "crewai.Crew.akickoff",
        new=AsyncMock(
            side_effect=Exception("Task failed guardrail validation after 3 retries.")
        ),
    ):
        message = await speaker.speak(addressed_by=asking)
    assert message.player_name == "A"
    assert message.text == DECLINED_TO_RESPOND
    assert message.addressed_to is None


async def test_reraises_non_guardrail_errors():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    with patch(
        "crewai.Crew.akickoff",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        try:
            await speaker.speak(addressed_by=None)
        except RuntimeError as exc:
            assert str(exc) == "boom"
        else:
            raise AssertionError("expected RuntimeError to propagate")


async def test_discards_addressed_to_from_the_analyst_on_decline():
    """Even if the analyst task somehow returns an address for a decline (it
    still runs -- see the spec's resolved decision to keep one uniform
    two-task Crew shape), a decline's recorded message must not carry it."""
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    with patch(
        "crewai.Crew.akickoff",
        new=AsyncMock(
            return_value=_crew_result(
                _SpeakerOutput(has_something_to_say=False),
                _AddressResolution(addressed_to="B"),
            )
        ),
    ):
        message = await speaker.speak(addressed_by=None)
    assert message is None
