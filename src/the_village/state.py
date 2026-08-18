from typing import Literal

from pydantic import BaseModel

PlayerType = Literal["user", "villager", "werewolf"]

WEEKDAYS = [
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
]


class Villager(BaseModel):
    name: str
    player_type: PlayerType
    is_pack_leader: bool = False
    is_alive: bool = True


class Death(BaseModel):
    name: str
    day_number: int


class DiscussionMessage(BaseModel):
    day_number: int
    speaker: str
    message: str
    addressed_to: str | None = None


class VoteRecord(BaseModel):
    day_number: int
    voter: str
    target: str | None = None


class Lynching(BaseModel):
    name: str
    day_number: int


class GameState(BaseModel):
    player_name: str = ""
    day_number: int = 1
    villagers: list[Villager] = []
    deaths: list[Death] = []
    discussion: list[DiscussionMessage] = []
    votes: list[VoteRecord] = []
    lynchings: list[Lynching] = []
