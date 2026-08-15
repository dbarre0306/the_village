import random

from the_village.state import Death, GameState


def resolve_night_one(
    state: GameState, rng: random.Random | None = None
) -> GameState:
    rng = rng or random.Random()

    eligible = [
        v for v in state.villagers if v.player_type == "villager" and v.is_alive
    ]
    victim = rng.choice(eligible)
    victim.is_alive = False

    new_day = state.day_number + 1
    state.deaths.append(Death(name=victim.name, day_number=new_day))
    state.day_number = new_day

    return state
