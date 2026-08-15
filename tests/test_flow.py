from the_village.main import VillageFlow


def test_village_flow_produces_valid_night_one_result():
    flow = VillageFlow()
    flow.kickoff(inputs={"player_name": "Dana"})
    state = flow.state

    assert len(state.villagers) == 7
    assert len(state.deaths) == 1

    death = state.deaths[0]
    assert death.day_number == 2

    killed = next(v for v in state.villagers if v.name == death.name)
    assert killed.player_type == "villager"
    assert killed.is_alive is False

    player = next(v for v in state.villagers if v.player_type == "user")
    assert player.name == "Dana"
    assert player.is_alive is True
