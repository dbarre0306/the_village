# tests/test_flow.py
import asyncio
from unittest.mock import patch

from the_village.core.bridge import FlowStatus, GameOverResult, PlayerInput, SessionBridge
from the_village.core.state import Player
from the_village.core.village_flow import VillageFlow
from the_village.voting import VoteOutcome

from conftest import _decline_and_abstain_akickoff


async def test_village_flow_produces_valid_night_one_result_and_pauses_for_discussion():
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)
    task = asyncio.create_task(flow.kickoff_async(inputs={"user_player_name": "Dana"}))

    # announce_death() puts the victim's name (a bare str) on the outbox as
    # the very first item -- nothing else is queued before it, since
    # run_discussion() (which queues DiscussionMessage/FlowStatus items)
    # only starts after this pause is resolved.
    player_found_dead = await bridge.outbox.get()
    bridge.resolve_input(PlayerInput())

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    state = flow.state
    assert len(state.players) == 7
    assert state.day_number == 1
    assert state.current_day.player_found_dead == player_found_dead

    killed = next(v for v in state.players if v.name == player_found_dead)
    assert killed.player_type == "villager"
    assert killed.is_alive is False

    player = next(v for v in state.players if v.player_type == "user")
    assert player.name == "Dana"
    assert player.is_alive is True


async def test_village_flow_builds_player_agents_onto_the_bridge():
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)
    task = asyncio.create_task(flow.kickoff_async(inputs={"user_player_name": "Dana"}))

    await bridge.outbox.get()
    bridge.resolve_input(PlayerInput())

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    ai_names = {
        v.name for v in flow.state.players if v.player_type in ("villager", "werewolf")
    }
    assert set(bridge.player_agents.keys()) == ai_names


async def test_village_flow_reaches_voting_complete_with_an_outcome():
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)
    task = asyncio.create_task(flow.kickoff_async(inputs={"user_player_name": "Dana"}))

    with patch("crewai.Crew.akickoff", new=_decline_and_abstain_akickoff), patch("crewai.Crew.kickoff_async", new=_decline_and_abstain_akickoff):
        await bridge.outbox.get()  # death announcement
        bridge.resolve_input(PlayerInput())  # -> begin discussion

        outcome = None
        while True:
            item = await bridge.outbox.get()
            if item == FlowStatus.DISCUSSION_COMPLETE:
                bridge.resolve_input(PlayerInput())  # -> begin voting
            elif item == FlowStatus.WAITING_FOR_VOTE:
                bridge.resolve_input(PlayerInput(text=None))  # player abstains
            elif isinstance(item, VoteOutcome):
                outcome = item
            elif item == FlowStatus.VOTING_COMPLETE:
                # check_winner_after_lynching's advance_day() runs as a
                # separate flow step after VOTING_COMPLETE is already on the
                # outbox -- draining the next night's death (queued only
                # after advance_day() has run) guarantees it's happened
                # before the assertions below, avoiding a race against the
                # background flow task.
                await bridge.outbox.get()
                break
            elif item in (FlowStatus.WAITING_FOR_TURN, FlowStatus.WAITING_FOR_ANSWER):
                bridge.resolve_input(PlayerInput(text=None))

        # The default 7-player roster needs 5 kills to reach werewolf
        # parity, so the game genuinely isn't over yet at this point --
        # cancel rather than waiting for a natural end this test won't reach.
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert outcome is not None
    assert outcome.day_number == 1
    assert outcome.tally == {}
    assert outcome.lynched is None
    assert flow.state.day_number == 2


async def test_village_flow_kills_and_announces_a_second_victim_after_voting():
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)
    task = asyncio.create_task(flow.kickoff_async(inputs={"user_player_name": "Dana"}))

    with patch("crewai.Crew.akickoff", new=_decline_and_abstain_akickoff), patch("crewai.Crew.kickoff_async", new=_decline_and_abstain_akickoff):
        await bridge.outbox.get()  # night one's death announcement
        bridge.resolve_input(PlayerInput())  # -> begin discussion

        second_death = None
        while True:
            item = await bridge.outbox.get()
            if item == FlowStatus.DISCUSSION_COMPLETE:
                bridge.resolve_input(PlayerInput())  # -> begin voting
            elif item == FlowStatus.WAITING_FOR_VOTE:
                bridge.resolve_input(PlayerInput(text=None))  # player abstains
            elif item == FlowStatus.VOTING_COMPLETE:
                second_death = await bridge.outbox.get()
                break
            elif item in (FlowStatus.WAITING_FOR_TURN, FlowStatus.WAITING_FOR_ANSWER):
                bridge.resolve_input(PlayerInput(text=None))

        # The default 7-player roster needs 5 kills to reach werewolf
        # parity, so the game genuinely isn't over yet at this point --
        # cancel rather than waiting for a natural end this test won't reach.
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    state = flow.state
    assert second_death is not None
    assert state.current_day.player_found_dead == second_death

    killed = next(v for v in state.players if v.name == second_death)
    assert killed.player_type in ("villager", "user")
    assert killed.is_alive is False


async def test_village_flow_ends_the_game_when_a_night_kill_reaches_werewolf_parity():
    # 3 living non-werewolves (Dana, A, D) vs 2 werewolves -- one villager
    # dying tonight brings it to 2 vs 2, ending the game right after the
    # death is announced and "Begin" is clicked, before discussion starts.
    custom_roster = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="D", player_type="villager"),
        Player(name="B", player_type="werewolf", is_pack_leader=True),
        Player(name="C", player_type="werewolf"),
    ]
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)

    with patch(
        "the_village.core.village_flow.build_initial_roster", return_value=custom_roster
    ):
        task = asyncio.create_task(
            flow.kickoff_async(inputs={"user_player_name": "Dana"})
        )

        await bridge.outbox.get()  # the night's death announcement
        bridge.resolve_input(PlayerInput())  # -> Begin

        result = await bridge.outbox.get()

        await task  # the flow reaches its natural end -- no cancellation needed

    assert isinstance(result, GameOverResult)
    assert result.winner == "werewolves"
    assert set(result.werewolf_names) == {"B", "C"}
    assert flow.state.winner == "werewolves"


async def test_village_flow_ends_the_game_when_a_lynch_eliminates_the_last_werewolf():
    # 3 living non-werewolves (Dana, A, D) vs 1 werewolf (W) -- night one's
    # kill removes one villager (2 vs 1, game continues); the human then
    # votes to lynch W, leaving zero living werewolves.
    custom_roster = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="D", player_type="villager"),
        Player(name="W", player_type="werewolf", is_pack_leader=True),
    ]
    bridge = SessionBridge()
    flow = VillageFlow(bridge=bridge)

    with patch(
        "the_village.core.village_flow.build_initial_roster", return_value=custom_roster
    ), patch("crewai.Crew.akickoff", new=_decline_and_abstain_akickoff), patch("crewai.Crew.kickoff_async", new=_decline_and_abstain_akickoff):
        task = asyncio.create_task(
            flow.kickoff_async(inputs={"user_player_name": "Dana"})
        )

        await bridge.outbox.get()  # night one's death announcement
        bridge.resolve_input(PlayerInput())  # -> begin discussion

        result = None
        while result is None:
            item = await bridge.outbox.get()
            if item == FlowStatus.DISCUSSION_COMPLETE:
                bridge.resolve_input(PlayerInput())  # -> begin voting
            elif item == FlowStatus.WAITING_FOR_VOTE:
                bridge.resolve_input(PlayerInput(text="W"))  # vote to lynch the werewolf
            elif isinstance(item, GameOverResult):
                result = item
            elif item in (FlowStatus.WAITING_FOR_TURN, FlowStatus.WAITING_FOR_ANSWER):
                bridge.resolve_input(PlayerInput(text=None))

        await task

    assert result.winner == "villagers"
    assert result.werewolf_names == ["W"]
    assert flow.state.winner == "villagers"
    assert flow.state.day_number == 1  # no dangling extra Day once the game ended
