#!/usr/bin/env python
from crewai.flow import Flow, listen, start

from the_village.night import resolve_night_one
from the_village.roster import build_initial_roster
from the_village.state import GameState


class VillageFlow(Flow[GameState]):
    @start()
    def setup_game(self):
        roster_state = build_initial_roster(self.state.player_name)
        self.state.day_number = roster_state.day_number
        self.state.villagers = roster_state.villagers

    @listen(setup_game)
    def run_night_one(self):
        resolve_night_one(self.state)


def kickoff():
    village_flow = VillageFlow()
    village_flow.kickoff(inputs={"player_name": "TestPlayer"})
    print(village_flow.state.model_dump_json(indent=2))


def plot():
    village_flow = VillageFlow()
    village_flow.plot()


if __name__ == "__main__":
    kickoff()
