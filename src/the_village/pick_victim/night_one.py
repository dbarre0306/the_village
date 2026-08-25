import random

from the_village.state import GameState


def resolve_night_one(
    state: GameState, rng: random.Random | None = None
) -> GameState:
    rng = rng or random.Random()

    eligible = [
        v for v in state.players if v.player_type == "villager" and v.is_alive
    ]
    victim = rng.choice(eligible)
    victim.is_alive = False

    state.current_day.player_found_dead = victim.name

    return state
