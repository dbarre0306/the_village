from types import SimpleNamespace

from the_village.state import Day, DiscussionMessage, GameState, Player
from the_village.voting import (
    VoteChoice,
    VoteOutcome,
    _build_vote_prompt,
    _format_lynchings,
    _resolve_target,
    cast_votes,
)


def test_resolve_target_rejects_self_vote():
    assert _resolve_target("A", ["A", "B"], exclude="A") is None


def test_resolve_target_rejects_unknown_name():
    assert _resolve_target("Ghost", ["A", "B"], exclude="A") is None


def test_resolve_target_accepts_valid_candidate():
    assert _resolve_target("B", ["A", "B"], exclude="A") == "B"


def test_resolve_target_treats_none_as_abstain():
    assert _resolve_target(None, ["A", "B"], exclude="A") is None


def test_vote_choice_defaults_to_abstain():
    assert VoteChoice().target is None


def test_vote_outcome_fields():
    outcome = VoteOutcome(day_number=2, votes=[], tally={"A": 1}, lynched="A")
    assert outcome.day_number == 2
    assert outcome.tally == {"A": 1}
    assert outcome.lynched == "A"


def test_format_lynchings_with_no_lynchings():
    state = GameState(player_name="Dana")
    assert _format_lynchings(state) == "(No one has been lynched yet.)"


def test_format_lynchings_lists_each_lynching():
    state = GameState(
        player_name="Dana", days=[Day(day_number=1, player_lynched="C")]
    )
    assert _format_lynchings(state) == "C was lynched by the village on Sunday."


def test_build_vote_prompt_lists_candidates():
    state = GameState(player_name="Dana")
    prompt = _build_vote_prompt(state, ["A", "B"])
    assert "A, B" in prompt


def test_build_vote_prompt_includes_full_multi_day_discussion_history():
    state = GameState(
        player_name="Dana",
        days=[
            Day(day_number=1, discussion=[DiscussionMessage(speaker="A", text="yesterday's claim")]),
            Day(day_number=2, discussion=[DiscussionMessage(speaker="B", text="today's claim")]),
        ],
    )
    prompt = _build_vote_prompt(state, ["A", "B"])
    assert "yesterday's claim" in prompt
    assert "today's claim" in prompt


def test_build_vote_prompt_includes_lynching_history():
    state = GameState(
        player_name="Dana", days=[Day(day_number=1, player_lynched="C")]
    )
    prompt = _build_vote_prompt(state, ["A", "B"])
    assert "C was lynched by the village on Sunday." in prompt


class ScriptedVoteAgent:
    def __init__(self, target):
        self._target = target

    def kickoff(self, messages, response_format=None):
        return SimpleNamespace(pydantic=VoteChoice(target=self._target))


def make_voting_state(day_number: int = 2) -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
        Player(name="C", player_type="villager"),
        Player(name="E", player_type="werewolf"),
    ]
    return GameState(
        player_name="Dana", players=players, days=[Day(day_number=day_number)]
    )


def test_majority_vote_lynches_the_top_target():
    state = make_voting_state()
    agents = {
        "A": ScriptedVoteAgent("B"),
        "B": ScriptedVoteAgent("B"),
        "C": ScriptedVoteAgent("B"),
        "E": ScriptedVoteAgent("B"),
    }

    outcome = cast_votes(state, agents, player_vote="B")

    assert outcome.lynched == "B"
    assert outcome.tally == {"B": 4}
    b = next(v for v in state.players if v.name == "B")
    assert b.is_alive is False
    assert state.current_day.player_lynched == "B"


def test_ai_votes_can_lynch_the_player():
    # The player is a candidate on every AI villager's ballot just like
    # anyone else, so a majority of AI votes against them must be able to
    # flip their is_alive flag -- this is reachable, in-scope behavior even
    # though the day 2+ game loop/win conditions are out of scope.
    state = make_voting_state()
    agents = {
        "A": ScriptedVoteAgent("Dana"),
        "B": ScriptedVoteAgent("Dana"),
        "C": ScriptedVoteAgent("Dana"),
        "E": ScriptedVoteAgent(None),
    }

    outcome = cast_votes(state, agents, player_vote=None)

    assert outcome.lynched == "Dana"
    dana = next(v for v in state.players if v.name == "Dana")
    assert dana.is_alive is False
    assert state.current_day.player_lynched == "Dana"


def test_tie_results_in_no_lynch():
    state = make_voting_state()
    agents = {
        "A": ScriptedVoteAgent("B"),
        "B": ScriptedVoteAgent("C"),
        "C": ScriptedVoteAgent("A"),
        "E": ScriptedVoteAgent("A"),
    }

    outcome = cast_votes(state, agents, player_vote="B")

    # tally: A=2 (from C, E), B=2 (from Dana, A) -> tied for the top
    assert outcome.lynched is None
    assert state.current_day.player_lynched is None
    assert all(v.is_alive for v in state.players)


def test_all_abstain_results_in_no_lynch():
    state = make_voting_state()
    agents = {name: ScriptedVoteAgent(None) for name in ["A", "B", "C", "E"]}

    outcome = cast_votes(state, agents, player_vote=None)

    assert outcome.lynched is None
    assert outcome.tally == {}


def test_ai_self_vote_is_normalized_to_abstain():
    state = make_voting_state()
    agents = {
        "A": ScriptedVoteAgent("A"),
        "B": ScriptedVoteAgent(None),
        "C": ScriptedVoteAgent(None),
        "E": ScriptedVoteAgent(None),
    }

    outcome = cast_votes(state, agents, player_vote=None)

    a_record = next(v for v in outcome.votes if v.voter == "A")
    assert a_record.target is None


def test_dead_villagers_excluded_from_voting_and_targets():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager", is_alive=False),
        Player(name="B", player_type="villager"),
    ]
    state = GameState(player_name="Dana", players=players, days=[Day(day_number=3)])
    agents = {"B": ScriptedVoteAgent("A")}

    outcome = cast_votes(state, agents, player_vote=None)

    assert "A" not in [record.voter for record in outcome.votes]
    b_record = next(v for v in outcome.votes if v.voter == "B")
    assert b_record.target is None  # A is dead, so an invalid target


def test_player_vote_used_directly_without_kickoff():
    state = make_voting_state()
    agents = {
        "A": ScriptedVoteAgent(None),
        "B": ScriptedVoteAgent(None),
        "C": ScriptedVoteAgent(None),
        "E": ScriptedVoteAgent(None),
    }

    outcome = cast_votes(state, agents, player_vote="B")

    dana_record = next(v for v in outcome.votes if v.voter == "Dana")
    assert dana_record.target == "B"


def test_votes_recorded_onto_the_current_day():
    state = make_voting_state(day_number=5)
    agents = {name: ScriptedVoteAgent(None) for name in ["A", "B", "C", "E"]}

    outcome = cast_votes(state, agents, player_vote=None)

    assert outcome.day_number == 5
    assert state.current_day.day_number == 5
    assert state.current_day.votes == outcome.votes


def test_prompt_passed_to_agents_includes_full_multi_day_discussion_history():
    state = make_voting_state()
    state.days = [
        Day(day_number=1, discussion=[DiscussionMessage(speaker="A", text="yesterday's claim")]),
        Day(day_number=2, discussion=[DiscussionMessage(speaker="B", text="today's claim")]),
    ]
    captured = {}

    class CapturingAgent:
        def kickoff(self, messages, response_format=None):
            captured["prompt"] = messages
            return SimpleNamespace(pydantic=VoteChoice(target=None))

    agents = {
        "A": CapturingAgent(),
        "B": ScriptedVoteAgent(None),
        "C": ScriptedVoteAgent(None),
        "E": ScriptedVoteAgent(None),
    }

    cast_votes(state, agents, player_vote=None)

    assert "yesterday's claim" in captured["prompt"]
    assert "today's claim" in captured["prompt"]
