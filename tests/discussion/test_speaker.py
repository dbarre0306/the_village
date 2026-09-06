from the_village.core.bridge import SessionBridge
from the_village.discussion.speaker import _AddressResolution, _Speaker
from the_village.core.state import GameState, Player


def make_discussion_state() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
    ]
    return GameState(user_player_name="Dana", players=players)


def make_speaker(state: GameState, player_name: str = "A") -> _Speaker:
    return _Speaker(state, SessionBridge(), player_name, analyst_agent=None)


def test_record_message_appends_to_current_day_and_returns_it():
    state = make_discussion_state()
    speaker = make_speaker(state, "A")

    msg = speaker._record_message("hello", addressed_to="B")

    assert state.current_day.discussion == [msg]
    assert msg.player_name == "A"
    assert msg.text == "hello"
    assert msg.addressed_to == "B"
    assert state.current_day.day_number == 1


def test_resolve_target_rejects_self_and_unknown_names():
    state = make_discussion_state()
    speaker = make_speaker(state, "A")

    assert speaker._resolve_target(None) is None
    assert speaker._resolve_target("A") is None
    assert speaker._resolve_target("Ghost") is None
    assert speaker._resolve_target("B") == "B"


def test_address_resolution_field_excludes_mere_accusations():
    description = _AddressResolution.model_fields["addressed_to"].description
    assert "directly asks a question of, or explicitly demands a response from" in description
    assert "Merely accusing or talking about a player" in description
    assert "doesn't count" in description
