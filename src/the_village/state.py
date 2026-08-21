from typing import Final, Literal

from pydantic import BaseModel

USER: Final = "user"
VILLAGER: Final = "villager"
WEREWOLF: Final = "werewolf"

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


class Player(BaseModel):
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
    players: list[Player] = []
    deaths: list[Death] = []
    discussion: list[DiscussionMessage] = []
    votes: list[VoteRecord] = []
    lynchings: list[Lynching] = []

    def ai_players(self) -> list[str]:
        return filter(lambda player: player.player_type in (VILLAGER, WEREWOLF), self.players)
    
