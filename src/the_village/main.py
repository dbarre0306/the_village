#!/usr/bin/env python
from pathlib import Path

from pydantic import BaseModel

from crewai.flow import Flow, listen, start

class VillageFlow(Flow):

    @start()
    def start(self):
        pass


def kickoff():
    village_flow = VillageFlow()
    village_flow.kickoff()


def plot():
    content_flow = VillageFlow()
    content_flow.plot()


if __name__ == "__main__":
    kickoff()
