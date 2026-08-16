import random

from the_village.state import GameState, Villager

VILLAGER_NAME_POOL = [
    "Alice",
    "Bruce",
    "Corin",
    "Della",
    "Edwin",
    "Fiora",
    "Garrick",
    "Hattie",
    "Lisa",
    "Joshua",
    "Kestrel",
    "Stephen",
]


def build_initial_roster(
    player_name: str, rng: random.Random | None = None
) -> GameState:
    rng = rng or random.Random()

    available_names = [
        name
        for name in VILLAGER_NAME_POOL
        if name.lower() != player_name.strip().lower()
    ]
    ai_names = rng.sample(available_names, 6)
    villagers = [Villager(name=player_name, player_type="user")]
    villagers += [
        Villager(name=name, player_type="villager") for name in ai_names
    ]

    werewolves = rng.sample(villagers[1:], 2)
    for werewolf in werewolves:
        werewolf.player_type = "werewolf"
    rng.choice(werewolves).is_pack_leader = True

    return GameState(player_name=player_name, day_number=1, villagers=villagers)
