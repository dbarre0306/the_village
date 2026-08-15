from the_village.state import WEEKDAYS, Death, GameState, Villager


def test_villager_defaults():
    villager = Villager(name="Alice", player_type="villager")
    assert villager.is_pack_leader is False
    assert villager.is_alive is True


def test_death_fields():
    death = Death(name="Alice", day_number=2)
    assert death.name == "Alice"
    assert death.day_number == 2


def test_game_state_defaults():
    state = GameState()
    assert state.player_name == ""
    assert state.day_number == 1
    assert state.villagers == []
    assert state.deaths == []


def test_weekdays_starts_on_sunday():
    assert WEEKDAYS[0] == "Sunday"
    assert len(WEEKDAYS) == 7
