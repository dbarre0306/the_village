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

    def is_werewolf(self, name: str) -> bool:
        player = next((player for player in self.players if player.name == name), None)
        if player is None:
            return False
        return player.is_werewolf

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
        return sum(
            1 for player in self.players if player.is_werewolf and player.is_alive
        )

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

    def known_facts(self, player_name: str = None) -> str:
        facts = [
            "# Known Facts",
            self.format_current_day(),
            self._format_deaths(),
            self._format_lynchings(),
            self._format_other_living_players(player_name),
            "",
            "## Daily History",
            self._format_daily_history(),
        ]
        return "\n".join(facts)

    def format_current_day(self) -> str:
        return f"Today is {_weekday(self.day_number)}."

    def _format_deaths(self) -> str:
        dead_days = [day for day in self.days if day.player_found_dead]
        if not dead_days:
            return "(No one has been killed by the werewolves yet.)"
        return "\n".join(
            f"{day.player_found_dead} was killed by the werewolves today."
            for day in dead_days
        )

    def _format_lynchings(self) -> str:
        lynched_days = [day for day in self.days if day.player_lynched]
        if not lynched_days:
            return "(No one has been lynched yet.)"
        return "\n".join(
            f"{day.player_lynched} was lynched by the village on {_weekday(day.day_number)}."
            for day in lynched_days
        )

    def _format_other_living_players(self, player_name: str | None) -> str:
        if player_name is None:
            return ""
        return f"Other living players: {', '.join(self.names_of_other_living_players(player_name))}"

    def _format_daily_history(self) -> str:
        history = map(lambda day: self._format_day_history(day), self.days)
        return "\n".join(history)

    def _format_day_history(self, day: Day) -> str:
        day_history = [
            f"### Day {day.day_number}: {_weekday(day.day_number)}",
            self._format_dead_person(day),
            self._format_discussion(day),
            self._format_voting(day),
            self._format_lynching(day),
        ]
        return "\n".join(day_history)

    def _format_dead_person(self, day: Day) -> str:
        if day.player_found_dead is None:
            return "No one was killed today"
        return f"{day.player_found_dead} was found dead in the morning. Killed by a werewolf."

    def _format_discussion(self, day: Day) -> str:
        if not day.discussion:
            return ""
        discussion = [
            "#### Discussion",
            "\n".join(f"{m.player_name}: {m.text}" for m in day.discussion),
        ]
        return "\n".join(discussion)

    def _format_voting(self, day: Day) -> str:
        if not day.votes:
            return ""
        votes = [
            "#### Lynching Votes",
            "\n".join(self._format_vote(vote) for vote in day.votes),
        ]
        return "\n".join(votes)

    def _format_vote(self, vote: VoteRecord) -> str:
        if vote.target_name is None:
            return f"{vote.voter_name} abstained from voting."
        return f"{vote.voter_name} voted to lynch {vote.target_name}."

    def _format_lynching(self, day: Day) -> str:
        if day.player_lynched is None:
            return ""
        return f"{day.player_lynched} was lynched by the village."
