import random

from the_village.night import resolve_night_one
from the_village.state import GameState, Villager


def make_state() -> GameState:
    villagers = [
        Villager(name="Dana", player_type="user"),
        Villager(name="A", player_type="villager"),
        Villager(name="B", player_type="villager"),
        Villager(name="C", player_type="villager"),
        Villager(name="D", player_type="villager"),
        Villager(name="E", player_type="werewolf", is_pack_leader=True),
        Villager(name="F", player_type="werewolf"),
    ]
    return GameState(player_name="Dana", day_number=1, villagers=villagers)


def test_kills_a_non_player_non_werewolf_villager():
    state = make_state()
    resolve_night_one(state, random.Random(1))

    assert len(state.deaths) == 1
    killed_name = state.deaths[0].name
    assert killed_name in {"A", "B", "C", "D"}


def test_killed_villager_marked_not_alive():
    state = make_state()
    resolve_night_one(state, random.Random(1))

    killed_name = state.deaths[0].name
    killed = next(v for v in state.villagers if v.name == killed_name)
    assert killed.is_alive is False


def test_player_and_werewolves_survive_night_one():
    state = make_state()
    resolve_night_one(state, random.Random(1))

    player = next(v for v in state.villagers if v.player_type == "user")
    werewolves = [v for v in state.villagers if v.player_type == "werewolf"]
    assert player.is_alive is True
    assert all(w.is_alive for w in werewolves)


def test_day_number_advances_to_two():
    state = make_state()
    resolve_night_one(state, random.Random(1))

    assert state.day_number == 2
    assert state.deaths[0].day_number == 2
