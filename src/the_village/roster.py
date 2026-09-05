import random

from the_village.personalities import PERSONALITIES
from the_village.state import GameState, Player

NUMBER_OF_PLAYERS = 7

PLAYER_NAME_POOL = [
    "Alice",
    "Bruce",
    "Emma",
    "Della",
    "Edwin",
    "Fiora",
    "Garrick",
    "Hattie",
    "Martha",
    "Joshua",
    "Kestrel",
    "Stephen",
    "Henry",
    "Joan",
]


def build_initial_roster(
    user_player_name: str, rng: random.Random | None = None
) -> list[Player]:
    rng = rng or random.Random()

    available_names = [
        name
        for name in PLAYER_NAME_POOL
        if name.lower() != user_player_name.strip().lower()
    ]
    ai_names = rng.sample(available_names, NUMBER_OF_PLAYERS)
    players = [Player(name=user_player_name, player_type="user")]
    players += [Player(name=name, player_type="villager") for name in ai_names]

    werewolves = rng.sample(players[1:], 2)
    for werewolf in werewolves:
        werewolf.player_type = "werewolf"
    rng.choice(werewolves).is_pack_leader = True

    villagers = [player for player in players if player.player_type == "villager"]
    personalities = rng.sample(list(PERSONALITIES.values()), len(villagers))
    for villager, personality in zip(villagers, personalities):
        villager.personality = personality

    return players
