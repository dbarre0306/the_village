import asyncio

from the_village.bridge import FlowStatus, GameOverResult, PlayerInput, SessionBridge


def test_flow_status_includes_voting_states():
    assert FlowStatus.WAITING_FOR_VOTE == "waiting_for_vote"
    assert FlowStatus.VOTING_COMPLETE == "voting_complete"


def test_resolve_input_is_a_noop_with_no_pending_wait():
    bridge = SessionBridge()
    assert bridge.resolve_input(PlayerInput(text="hi")) is False


async def test_wait_for_input_returns_the_resolved_value():
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)  # let wait_for_input reach its await point

    assert bridge.resolve_input(PlayerInput(text="hello")) is True
    assert await waiter == PlayerInput(text="hello")


async def test_resolve_input_is_a_noop_once_already_resolved():
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)

    bridge.resolve_input(PlayerInput(text="first"))

    assert bridge.resolve_input(PlayerInput(text="second")) is False
    assert await waiter == PlayerInput(text="first")


async def test_wait_for_input_clears_pending_input_after_resolving():
    bridge = SessionBridge()
    waiter = asyncio.create_task(bridge.wait_for_input())
    await asyncio.sleep(0)

    bridge.resolve_input(PlayerInput())
    await waiter

    assert bridge.pending_input is None


async def test_run_flow_pushes_flow_failed_onto_the_outbox_on_exception():
    from the_village.bridge import FlowFailed, run_flow

    bridge = SessionBridge()

    async def boom():
        raise ValueError("crew exploded")

    await run_flow(boom(), bridge)

    item = bridge.outbox.get_nowait()
    assert isinstance(item, FlowFailed)
    assert "crew exploded" in item.detail


async def test_run_flow_does_not_touch_the_outbox_on_success():
    from the_village.bridge import run_flow

    bridge = SessionBridge()

    async def fine():
        return None

    await run_flow(fine(), bridge)

    assert bridge.outbox.empty()


def test_game_over_result_carries_winner_and_werewolf_names():
    result = GameOverResult(winner="villagers", werewolf_names=["A", "B"])
    assert result.winner == "villagers"
    assert result.werewolf_names == ["A", "B"]
