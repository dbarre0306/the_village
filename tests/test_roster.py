import random

import pytest

from the_village.personalities import PERSONALITIES
from the_village.roster import PLAYER_NAME_POOL, build_initial_roster


def test_roster_has_eight_villagers():
    players = build_initial_roster("Dana", random.Random(1))
    assert len(players) == 8


def test_roster_has_one_user_two_werewolves_five_villagers():
    players = build_initial_roster("Dana", random.Random(1))
    by_type = {"user": 0, "werewolf": 0, "villager": 0}
    for player in players:
        by_type[player.player_type] += 1
    assert by_type == {"user": 1, "werewolf": 2, "villager": 5}


def test_player_is_first_villager_and_is_user_type():
    players = build_initial_roster("Dana", random.Random(1))
    assert players[0].name == "Dana"
    assert players[0].player_type == "user"


def test_exactly_one_pack_leader_among_werewolves():
    players = build_initial_roster("Dana", random.Random(1))
    werewolves = [v for v in players if v.player_type == "werewolf"]
    leaders = [v for v in werewolves if v.is_pack_leader]
    assert len(leaders) == 1


def test_ai_villager_names_come_from_pool_and_are_unique():
    players = build_initial_roster("Dana", random.Random(1))
    ai_names = [v.name for v in players if v.player_type != "user"]
    assert len(ai_names) == len(set(ai_names))
    assert all(name in PLAYER_NAME_POOL for name in ai_names)


def test_ai_villager_names_exclude_a_player_name_matching_the_pool():
    players = build_initial_roster("Alice", random.Random(1))
    ai_names = [v.name for v in players if v.player_type != "user"]
    assert "Alice" not in ai_names


def test_ai_villager_names_exclude_a_player_name_case_insensitively():
    players = build_initial_roster("alice", random.Random(1))
    ai_names = [v.name for v in players if v.player_type != "user"]
    assert "Alice" not in ai_names


@pytest.mark.parametrize("seed", range(50))
def test_player_is_never_targeted_and_never_duplicated_across_seeds(seed):
    players = build_initial_roster("Alice", random.Random(seed))
    assert players[0].player_type == "user"
    assert all(v.name != "Alice" for v in players[1:])


def test_ai_villagers_get_unique_personalities():
    players = build_initial_roster("Dana", random.Random(1))
    villagers = [p for p in players if p.player_type == "villager"]
    personalities = [v.personality for v in villagers]
    assert all(p is not None for p in personalities)
    assert len(personalities) == len(set(personalities))
    assert all(p in PERSONALITIES.values() for p in personalities)


def test_user_and_werewolves_have_no_personality():
    players = build_initial_roster("Dana", random.Random(1))
    non_villagers = [p for p in players if p.player_type != "villager"]
    assert all(p.personality is None for p in non_villagers)
