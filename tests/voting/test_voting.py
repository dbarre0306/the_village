import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from crewai import Agent

from the_village.core.bridge import FlowStatus, PlayerInput, SessionBridge
from the_village.core.state import Day, GameState, Player, VoteRecord
from the_village.voting.ai_voter import _AiVoter, _VoteChoice
from the_village.voting.human_voter import _HumanVoter
from the_village.voting.voting import Voting, tally_votes


def test_tally_votes_counts_targets_and_ignores_abstentions():
    votes = [
        VoteRecord(voter_name="Dana", target_name="A"),
        VoteRecord(voter_name="B", target_name="A"),
        VoteRecord(voter_name="C", target_name=None),
    ]
    assert tally_votes(votes) == {"A": 2}


def _stub_agent(name: str) -> Agent:
    """A minimal real Agent, keyed by role so a scripted akickoff can tell
    which villager's vote it's answering -- Task/Crew construction
    validates that `agent` fields are actual Agent instances, so a plain
    object() won't do, even though Crew.akickoff is mocked in these tests.
    """
    return Agent(role=name, goal="stub", backstory="stub")


def _stub_agents(names: list[str]) -> dict[str, Agent]:
    return {name: _stub_agent(name) for name in names}


def _crew_result(target):
    return SimpleNamespace(
        tasks_output=[SimpleNamespace(pydantic=_VoteChoice(target=target))]
    )


def _scripted_akickoff(choices: dict[str, str | None]):
    async def akickoff(crew):
        [agent] = crew.agents
        return _crew_result(choices.get(agent.role))

    return akickoff


def make_voting_state(day_number: int = 2) -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
        Player(name="C", player_type="villager"),
        Player(name="E", player_type="werewolf"),
    ]
    return GameState(
        user_player_name="Dana", players=players, days=[Day(day_number=day_number)]
    )


async def _run_voting(state: GameState, choices: dict[str, str | None], player_vote):
    agents = _stub_agents(["A", "B", "C", "E"])
    bridge = SessionBridge()
    voting = Voting(state=state, bridge=bridge, player_agents=agents)
    task = asyncio.create_task(voting.run())

    status = await bridge.outbox.get()
    assert status == FlowStatus.WAITING_FOR_VOTE
    bridge.resolve_input(PlayerInput(text=player_vote))

    with patch("crewai.Crew.akickoff", new=_scripted_akickoff(choices)):
        return await task


async def test_majority_vote_lynches_the_top_target():
    state = make_voting_state()

    outcome = await _run_voting(
        state, {"A": "B", "B": "B", "C": "B", "E": "B"}, player_vote="B"
    )

    assert outcome.lynched == "B"
    assert outcome.tally == {"B": 4}
    b = next(p for p in state.players if p.name == "B")
    assert b.is_alive is False
    assert state.current_day.player_lynched == "B"


async def test_ai_votes_can_lynch_the_player():
    state = make_voting_state()

    outcome = await _run_voting(
        state, {"A": "Dana", "B": "Dana", "C": "Dana", "E": None}, player_vote=None
    )

    assert outcome.lynched == "Dana"
    dana = next(p for p in state.players if p.name == "Dana")
    assert dana.is_alive is False
    assert state.current_day.player_lynched == "Dana"


async def test_tie_results_in_no_lynch():
    state = make_voting_state()

    # tally: A=2 (from C, E), B=2 (from Dana, A) -- tied for the top
    outcome = await _run_voting(
        state, {"A": "B", "B": "C", "C": "A", "E": "A"}, player_vote="B"
    )

    assert outcome.lynched is None
    assert state.current_day.player_lynched is None
    assert all(p.is_alive for p in state.players)


async def test_all_abstain_results_in_no_lynch():
    state = make_voting_state()

    outcome = await _run_voting(
        state, {"A": None, "B": None, "C": None, "E": None}, player_vote=None
    )

    assert outcome.lynched is None
    assert outcome.tally == {}


async def test_dead_villagers_excluded_from_voting_and_targets():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager", is_alive=False),
        Player(name="B", player_type="villager"),
    ]
    state = GameState(user_player_name="Dana", players=players, days=[Day(day_number=3)])
    agents = _stub_agents(["B"])
    bridge = SessionBridge()
    voting = Voting(state=state, bridge=bridge, player_agents=agents)
    task = asyncio.create_task(voting.run())

    await bridge.outbox.get()
    bridge.resolve_input(PlayerInput(text=None))

    with patch("crewai.Crew.akickoff", new=_scripted_akickoff({"B": "A"})):
        outcome = await task

    assert "A" not in [record.voter_name for record in outcome.votes]
    b_record = next(v for v in outcome.votes if v.voter_name == "B")
    assert b_record.target_name is None  # A is dead, so an invalid target


async def test_votes_recorded_onto_the_current_day():
    state = make_voting_state(day_number=5)

    outcome = await _run_voting(
        state, {"A": None, "B": None, "C": None, "E": None}, player_vote=None
    )

    assert outcome.day_number == 5
    assert state.current_day.day_number == 5
    assert state.current_day.votes == outcome.votes


def test_builds_a_human_voter_for_the_user_and_ai_voters_for_everyone_else():
    state = make_voting_state()
    agents = _stub_agents(["A", "B", "C", "E"])

    voting = Voting(state=state, bridge=SessionBridge(), player_agents=agents)

    assert isinstance(voting._voters["Dana"], _HumanVoter)
    assert isinstance(voting._voters["A"], _AiVoter)


def test_voting_order_puts_the_human_first_even_if_not_first_in_players():
    # Regression test: voting order must not depend on roster.py happening
    # to place the human first in state.players.
    players = [
        Player(name="A", player_type="villager"),
        Player(name="Dana", player_type="user"),
        Player(name="B", player_type="villager"),
    ]
    state = GameState(user_player_name="Dana", players=players, days=[Day(day_number=1)])
    agents = _stub_agents(["A", "B"])

    voting = Voting(state=state, bridge=SessionBridge(), player_agents=agents)

    assert voting._voting_order() == ["Dana", "A", "B"]


async def test_human_votes_before_any_ai_kickoff_even_if_not_first_in_players():
    # If the human weren't polled first, this would reach the real,
    # unpatched Crew.akickoff before the bridge ever asks for the vote.
    players = [
        Player(name="A", player_type="villager"),
        Player(name="Dana", player_type="user"),
        Player(name="B", player_type="villager"),
    ]
    state = GameState(user_player_name="Dana", players=players, days=[Day(day_number=1)])
    agents = _stub_agents(["A", "B"])
    bridge = SessionBridge()
    voting = Voting(state=state, bridge=bridge, player_agents=agents)
    task = asyncio.create_task(voting.run())

    status = await bridge.outbox.get()
    assert status == FlowStatus.WAITING_FOR_VOTE
    bridge.resolve_input(PlayerInput(text=None))

    with patch("crewai.Crew.akickoff", new=_scripted_akickoff({"A": None, "B": None})):
        outcome = await task

    assert outcome.votes[0].voter_name == "Dana"
