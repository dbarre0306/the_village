from typing import Final, Literal

from pydantic import BaseModel, Field

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


class DiscussionMessage(BaseModel):
    speaker: str
    message: str
    addressed_to: str | None = None


class VoteRecord(BaseModel):
    voter: str
    target: str | None = None


class Day(BaseModel):
    day_number: int
    player_killed: str | None = None
    discussion: list[DiscussionMessage] = []
    votes: list[VoteRecord] = []
    player_lynched: str | None = None


class GameState(BaseModel):
    player_name: str = ""
    players: list[Player] = []
    days: list[Day] = Field(default_factory=lambda: [Day(day_number=1)])

    def ai_players(self) -> list[str]:
        return filter(lambda player: player.player_type in (VILLAGER, WEREWOLF), self.players)

    @property
    def day_number(self) -> int:
        return self.days[-1].day_number

    @property
    def current_day(self) -> Day:
        return self.days[-1]

    def advance_day(self, player_killed: str | None = None) -> Day:
        new_day = Day(day_number=self.day_number + 1, player_killed=player_killed)
        self.days.append(new_day)
        return new_day
