from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from crewai import Agent

from the_village.bridge import SessionBridge
from the_village.discussion.ai_speaker import AiSpeaker, SpeakerOutput
from the_village.discussion.speaker import DECLINED_TO_RESPOND, AddressResolution
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


def make_ai_speaker(state: GameState, player_name: str = "A") -> AiSpeaker:
    return AiSpeaker(
        state, SessionBridge(), player_name, _stub_agent(), _stub_agent()
    )


def test_speaker_output_has_no_addressed_to_field():
    assert "addressed_to" not in SpeakerOutput.model_fields


def test_speak_prompt_forbids_unfounded_behavior_claims():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert 'seems focused on' in prompt
    assert "never comment on who has or hasn't spoken yet" in prompt


async def test_returns_none_on_scheduled_decline():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    with patch(
        "crewai.Crew.akickoff",
        new=AsyncMock(
            return_value=_crew_result(SpeakerOutput(has_something_to_say=False), None)
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
            return_value=_crew_result(SpeakerOutput(has_something_to_say=False), None)
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
                SpeakerOutput(has_something_to_say=True, text="I saw B leave."),
                AddressResolution(addressed_to="B"),
            )
        ),
    ):
        message = await speaker.speak(addressed_by=None)
    assert message.text == "I saw B leave."
    assert message.addressed_to == "B"
    assert state.current_day.discussion == [message]


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
                SpeakerOutput(has_something_to_say=False),
                AddressResolution(addressed_to="B"),
            )
        ),
    ):
        message = await speaker.speak(addressed_by=None)
    assert message is None
