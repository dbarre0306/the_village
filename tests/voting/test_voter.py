from the_village.bridge import SessionBridge
from the_village.state import GameState, Player
from the_village.voting.voter import _Voter


def make_voting_state() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
    ]
    return GameState(user_player_name="Dana", players=players)


def make_voter(state: GameState, player_name: str = "A") -> _Voter:
    return _Voter(state, SessionBridge(), player_name)


def test_resolve_target_rejects_self_and_unknown_names():
    state = make_voting_state()
    voter = make_voter(state, "A")

    assert voter._resolve_target(None) is None
    assert voter._resolve_target("A") is None
    assert voter._resolve_target("Ghost") is None
    assert voter._resolve_target("B") == "B"


def test_resolve_target_rejects_dead_villagers():
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager", is_alive=False),
    ]
    state = GameState(user_player_name="Dana", players=players)
    voter = make_voter(state, "A")

    assert voter._resolve_target("B") is None


def test_record_vote_appends_to_current_day():
    state = make_voting_state()
    voter = make_voter(state, "A")

    voter._record_vote("B")

    assert len(state.current_day.votes) == 1
    record = state.current_day.votes[0]
    assert record.voter_name == "A"
    assert record.target_name == "B"


async def test_cast_calls_the_override_point_then_records_its_target():
    state = make_voting_state()

    class _StubVoter(_Voter):
        async def _cast(self):
            return "B"

    voter = _StubVoter(state, SessionBridge(), "A")
    await voter.cast()

    assert state.current_day.votes[0].voter_name == "A"
    assert state.current_day.votes[0].target_name == "B"
