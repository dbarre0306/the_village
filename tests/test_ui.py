from the_village.state import Death, GameState, Villager
from the_village.ui import format_deaths_panel, format_event_log


def make_state_with_one_death() -> GameState:
    villagers = [
        Villager(name="Dana", player_type="user"),
        Villager(name="A", player_type="villager", is_alive=False),
    ]
    return GameState(
        player_name="Dana",
        day_number=2,
        villagers=villagers,
        deaths=[Death(name="A", day_number=2)],
    )


def test_format_event_log_with_no_deaths():
    state = GameState(player_name="Dana", day_number=1)
    assert format_event_log(state) == "Nothing has happened yet."


def test_format_event_log_with_a_death():
    state = make_state_with_one_death()
    assert format_event_log(state) == "Monday morning: A was found dead."


def test_format_deaths_panel_with_no_deaths():
    state = GameState(player_name="Dana", day_number=1)
    assert format_deaths_panel(state) == "No one has been killed yet."


def test_format_deaths_panel_with_a_death():
    state = make_state_with_one_death()
    assert format_deaths_panel(state) == "Monday: A"
