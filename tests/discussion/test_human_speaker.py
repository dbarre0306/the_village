import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from crewai import Agent

from the_village.bridge import FlowStatus, PlayerInput, SessionBridge
from the_village.discussion.human_speaker import _HumanSpeaker
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
    return Agent(role="Stub", goal="stub", backstory="stub")


def _crew_result(*pydantic_outputs):
    return SimpleNamespace(
        tasks_output=[SimpleNamespace(pydantic=p) for p in pydantic_outputs]
    )


def make_human_speaker(state: GameState, bridge: SessionBridge) -> _HumanSpeaker:
    return _HumanSpeaker(state, bridge, "Dana", _stub_agent())


async def test_returns_none_on_scheduled_pass():
    # SessionBridge.resolve_input() is a no-op until wait_for_input() has a
    # pending future (see test_bridge.py), so the task must reach its await
    # point (a sleep(0) yield) before we resolve it -- calling resolve_input
    # first would hang forever.
    state = make_discussion_state()
    bridge = SessionBridge()
    speaker = make_human_speaker(state, bridge)
    task = asyncio.create_task(speaker.speak(addressed_by=None))
    await asyncio.sleep(0)
    bridge.resolve_input(PlayerInput(text=None))
    message = await task
    assert message is None
    assert state.current_day.discussion == []


async def test_records_decline_placeholder_when_owed_a_reply():
    state = make_discussion_state()
    bridge = SessionBridge()
    speaker = make_human_speaker(state, bridge)
    asking = speaker._record_message("Where were you?", addressed_to="Dana")
    task = asyncio.create_task(speaker.speak(addressed_by=asking))
    await asyncio.sleep(0)
    bridge.resolve_input(PlayerInput(text=None))
    message = await task
    assert message.text == DECLINED_TO_RESPOND
    assert message.addressed_to is None


async def test_resolves_address_via_the_analyst():
    state = make_discussion_state()
    bridge = SessionBridge()
    speaker = make_human_speaker(state, bridge)
    with patch(
        "crewai.Crew.akickoff",
        new=AsyncMock(return_value=_crew_result(_AddressResolution(addressed_to="B"))),
    ):
        task = asyncio.create_task(speaker.speak(addressed_by=None))
        await asyncio.sleep(0)
        bridge.resolve_input(PlayerInput(text="B, where were you?"))
        message = await task
    assert message.player_name == "Dana"
    assert message.text == "B, where were you?"
    assert message.addressed_to == "B"


async def test_puts_waiting_for_turn_before_awaiting_input_when_unaddressed():
    state = make_discussion_state()
    bridge = SessionBridge()
    speaker = make_human_speaker(state, bridge)
    task = asyncio.create_task(speaker.speak(addressed_by=None))

    status = await bridge.outbox.get()

    assert status == FlowStatus.WAITING_FOR_TURN

    bridge.resolve_input(PlayerInput(text=None))
    await task


async def test_puts_waiting_for_answer_before_awaiting_input_when_addressed():
    state = make_discussion_state()
    bridge = SessionBridge()
    speaker = make_human_speaker(state, bridge)
    asking = speaker._record_message("Where were you?", addressed_to="Dana")
    task = asyncio.create_task(speaker.speak(addressed_by=asking))

    status = await bridge.outbox.get()

    assert status == FlowStatus.WAITING_FOR_ANSWER

    bridge.resolve_input(PlayerInput(text=None))
    await task
