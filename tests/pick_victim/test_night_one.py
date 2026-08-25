import random

import pytest

from the_village.pick_victim import resolve_night_one
from the_village.roster import build_initial_roster
from the_village.state import GameState, Player


def make_state() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
        Player(name="C", player_type="villager"),
        Player(name="D", player_type="villager"),
        Player(name="E", player_type="werewolf", is_pack_leader=True),
        Player(name="F", player_type="werewolf"),
    ]
    return GameState(user_player_name="Dana", players=players)


def test_kills_a_non_player_non_werewolf_villager():
    state = make_state()
    resolve_night_one(state, random.Random(1))

    assert len(state.days) == 2
    killed_name = state.current_day.player_found_dead
    assert killed_name in {"A", "B", "C", "D"}


def test_killed_villager_marked_not_alive():
    state = make_state()
    resolve_night_one(state, random.Random(1))

    killed_name = state.current_day.player_found_dead
    killed = next(v for v in state.players if v.name == killed_name)
    assert killed.is_alive is False


def test_player_and_werewolves_survive_night_one():
    state = make_state()
    resolve_night_one(state, random.Random(1))

    player = next(v for v in state.players if v.player_type == "user")
    werewolves = [v for v in state.players if v.player_type == "werewolf"]
    assert player.is_alive is True
    assert all(w.is_alive for w in werewolves)


def test_day_number_advances_to_two():
    state = make_state()
    resolve_night_one(state, random.Random(1))

    assert state.day_number == 2


@pytest.mark.parametrize("seed", range(50))
def test_player_is_never_killed_and_stays_alive_across_seeds(seed):
    state = build_initial_roster("Alice", random.Random(seed))
    resolve_night_one(state, random.Random(seed))

    assert state.current_day.player_found_dead != "Alice"
    assert state.players[0].is_alive is True
