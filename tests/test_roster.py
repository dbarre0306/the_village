import random

import pytest

from the_village.roster import VILLAGER_NAME_POOL, build_initial_roster


def test_roster_has_seven_villagers():
    state = build_initial_roster("Dana", random.Random(1))
    assert len(state.villagers) == 7


def test_roster_has_one_user_two_werewolves_four_villagers():
    state = build_initial_roster("Dana", random.Random(1))
    by_type = {"user": 0, "werewolf": 0, "villager": 0}
    for villager in state.villagers:
        by_type[villager.player_type] += 1
    assert by_type == {"user": 1, "werewolf": 2, "villager": 4}


def test_player_is_first_villager_and_is_user_type():
    state = build_initial_roster("Dana", random.Random(1))
    assert state.villagers[0].name == "Dana"
    assert state.villagers[0].player_type == "user"


def test_exactly_one_pack_leader_among_werewolves():
    state = build_initial_roster("Dana", random.Random(1))
    werewolves = [v for v in state.villagers if v.player_type == "werewolf"]
    leaders = [v for v in werewolves if v.is_pack_leader]
    assert len(leaders) == 1


def test_ai_villager_names_come_from_pool_and_are_unique():
    state = build_initial_roster("Dana", random.Random(1))
    ai_names = [v.name for v in state.villagers if v.player_type != "user"]
    assert len(ai_names) == len(set(ai_names))
    assert all(name in VILLAGER_NAME_POOL for name in ai_names)


def test_ai_villager_names_exclude_a_player_name_matching_the_pool():
    state = build_initial_roster("Alice", random.Random(1))
    ai_names = [v.name for v in state.villagers if v.player_type != "user"]
    assert "Alice" not in ai_names


def test_ai_villager_names_exclude_a_player_name_case_insensitively():
    state = build_initial_roster("alice", random.Random(1))
    ai_names = [v.name for v in state.villagers if v.player_type != "user"]
    assert "Alice" not in ai_names


def test_day_number_starts_at_one():
    state = build_initial_roster("Dana", random.Random(1))
    assert state.day_number == 1


@pytest.mark.parametrize("seed", range(50))
def test_player_is_never_targeted_and_never_duplicated_across_seeds(seed):
    state = build_initial_roster("Alice", random.Random(seed))
    assert state.villagers[0].player_type == "user"
    assert all(v.name != "Alice" for v in state.villagers[1:])
