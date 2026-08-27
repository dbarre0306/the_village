from typing import Final, Literal

from pydantic import BaseModel, Field

USER: Final = "user"
VILLAGER: Final = "villager"
WEREWOLF: Final = "werewolf"

PlayerType = Literal["user", "villager", "werewolf"]

Winner = Literal["villagers", "werewolves"]

WEEKDAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def _weekday(day_number: int) -> str:
    return WEEKDAYS[(day_number - 1) % 7]


class Player(BaseModel):
    name: str
    player_type: PlayerType
    is_pack_leader: bool = False
    is_alive: bool = True

    @property
    def is_werewolf(self):
        return self.player_type == WEREWOLF

    @property
    def is_not_werewolf(self):
        return self.player_type != WEREWOLF


class DiscussionMessage(BaseModel):
    player_name: str
    text: str
    addressed_to: str | None = None


class VoteRecord(BaseModel):
    voter_name: str
    target_name: str | None = None


class Day(BaseModel):
    day_number: int
    player_found_dead: str | None = None
    discussion: list[DiscussionMessage] = []
    votes: list[VoteRecord] = []
    player_lynched: str | None = None


class GameState(BaseModel):
    user_player_name: str = ""
    players: list[Player] = []
    days: list[Day] = Field(default_factory=lambda: [Day(day_number=1)])
    winner: Winner | None = None

    @property
    def day_number(self) -> int:
        return self.days[-1].day_number

    @property
    def current_day(self) -> Day:
        return self.days[-1]

    def ai_players(self) -> list[str]:
        return filter(
            lambda player: player.player_type in (VILLAGER, WEREWOLF), self.players
        )

    def is_human_player(self, name: str) -> bool:
        return name == self.user_player_name

    def advance_day(self) -> Day:
        new_day = Day(day_number=self.day_number + 1)
        self.days.append(new_day)
        return new_day

    def names_of_living_players(self) -> list[str]:
        return [player.name for player in self.players if player.is_alive]

    def names_of_dead_players(self) -> list[str]:
        return [player.name for player in self.players if not player.is_alive]

    def names_of_other_living_players(self, player_name: str) -> list[str]:
        return [name for name in self.names_of_living_players() if name != player_name]

    def living_werewolves_count(self) -> int:
        return sum(1 for player in self.players if player.is_werewolf and player.is_alive)

    def living_non_werewolves_count(self) -> int:
        return sum(
            1 for player in self.players if player.is_not_werewolf and player.is_alive
        )

    def determine_winner(self) -> Winner | None:
        if self.living_werewolves_count() == 0:
            return "villagers"
        if self.living_werewolves_count() >= self.living_non_werewolves_count():
            return "werewolves"
        return None

    def werewolf_names(self) -> list[str]:
        return [player.name for player in self.players if player.is_werewolf]

    def last_player_to_speak(self) -> str | None:
        if not self.current_day.discussion:
            return None
        return self.current_day.discussion[-1].player_name

    def is_last_player_to_speak(self, player_name: str) -> bool:
        return player_name == self.last_player_to_speak()

    def format_current_day(self) -> str:
        return f"Today is {_weekday(self.day_number)}."

    def format_deaths(self) -> str:
        dead_days = [day for day in self.days if day.player_found_dead]
        if not dead_days:
            return "(No one has been killed by the werewolves yet.)"
        return "\n".join(
            f"{day.player_found_dead} was killed by the werewolves on {_weekday(day.day_number)}."
            for day in dead_days
        )

    def format_lynchings(self) -> str:
        lynched_days = [day for day in self.days if day.player_lynched]
        if not lynched_days:
            return "(No one has been lynched yet.)"
        return "\n".join(
            f"{day.player_lynched} was lynched by the village on {_weekday(day.day_number)}."
            for day in lynched_days
        )

    def format_history(self) -> str:
        messages = [message for day in self.days for message in day.discussion]
        if not messages:
            return "(No discussion has happened yet.)"
        return "\n".join(f"{m.player_name}: {m.text}" for m in messages)
