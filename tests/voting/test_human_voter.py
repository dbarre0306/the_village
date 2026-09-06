import asyncio

from the_village.core.bridge import FlowStatus, PlayerInput, SessionBridge
from the_village.core.state import GameState, Player
from the_village.voting.human_voter import _HumanVoter


def make_voting_state() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
    ]
    return GameState(user_player_name="Dana", players=players)


def make_human_voter(state: GameState, bridge: SessionBridge) -> _HumanVoter:
    return _HumanVoter(state, bridge, "Dana")


async def test_puts_waiting_for_vote_before_awaiting_input():
    state = make_voting_state()
    bridge = SessionBridge()
    voter = make_human_voter(state, bridge)
    task = asyncio.create_task(voter._cast())

    status = await bridge.outbox.get()
    assert status == FlowStatus.WAITING_FOR_VOTE

    bridge.resolve_input(PlayerInput(text=None))
    await task


async def test_resolves_target_from_player_input():
    state = make_voting_state()
    bridge = SessionBridge()
    voter = make_human_voter(state, bridge)
    task = asyncio.create_task(voter._cast())
    await bridge.outbox.get()

    bridge.resolve_input(PlayerInput(text="A"))
    target = await task

    assert target == "A"


async def test_self_vote_normalizes_to_abstain():
    state = make_voting_state()
    bridge = SessionBridge()
    voter = make_human_voter(state, bridge)
    task = asyncio.create_task(voter._cast())
    await bridge.outbox.get()

    bridge.resolve_input(PlayerInput(text="Dana"))
    target = await task

    assert target is None


async def test_none_input_is_abstain():
    state = make_voting_state()
    bridge = SessionBridge()
    voter = make_human_voter(state, bridge)
    task = asyncio.create_task(voter._cast())
    await bridge.outbox.get()

    bridge.resolve_input(PlayerInput(text=None))
    target = await task

    assert target is None
