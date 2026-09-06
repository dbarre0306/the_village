from crewai import Agent
from pydantic import BaseModel

from the_village.core.bridge import SessionBridge
from the_village.core.state import GameState, VoteRecord

from .human_voter import _HumanVoter
from .villager_voter import _VillagerVoter
from .voter import _Voter
from .werewolf_voter import _WerewolfVoter


class VoteOutcome(BaseModel):
    day_number: int
    votes: list[VoteRecord]
    tally: dict[str, int]
    lynched: str | None = None


def tally_votes(votes: list[VoteRecord]) -> dict[str, int]:
    tally: dict[str, int] = {}
    for vote in votes:
        if vote.target_name is not None:
            tally[vote.target_name] = tally.get(vote.target_name, 0) + 1
    return tally


class Voting:

    def __init__(
        self,
        state: GameState,
        bridge: SessionBridge,
        player_agents: dict[str, Agent],
    ):
        self._state = state
        self._bridge = bridge
        self._player_agents = player_agents
        self._voters = self._build_voters()

    def _build_voters(self) -> dict[str, _Voter]:
        living_players = self._state.living_player_names()
        return {name: self._build_voter(name) for name in living_players}

    def _build_voter(self, player_name: str) -> _Voter:
        if self._state.is_human_player(player_name):
            return _HumanVoter(self._state, self._bridge, player_name)
        if self._state.is_werewolf(player_name):
            return _WerewolfVoter(
                self._state,
                self._bridge,
                player_name,
                self._player_agents[player_name],
            )
        return _VillagerVoter(
            self._state,
            self._bridge,
            player_name,
            self._player_agents[player_name],
        )

    async def run(self) -> VoteOutcome:
        for player_name in self._voting_order():
            await self._voters[player_name].cast()
        return self._tally()

    def _voting_order(self) -> list[str]:
        # The human must vote first for the instant-ballot UX, ahead of any
        # AI kickoff. Enforced explicitly here rather than assumed from
        # state.players' ordering, so it can't silently break if that
        # ordering changes elsewhere.
        living_players = self._state.living_player_names()
        human, ai = [], []
        for name in living_players:
            (human if self._state.is_human_player(name) else ai).append(name)
        return human + ai

    def _tally(self) -> VoteOutcome:
        votes = self._state.current_day.votes
        tally = tally_votes(votes)

        lynched: str | None = None
        if tally:
            top_count = max(tally.values())
            top_targets = [name for name, count in tally.items() if count == top_count]
            if len(top_targets) == 1:
                lynched = top_targets[0]

        if lynched is not None:
            player = next(p for p in self._state.players if p.name == lynched)
            player.is_alive = False
            self._state.current_day.player_lynched = lynched

        return VoteOutcome(
            day_number=self._state.day_number,
            votes=votes,
            tally=tally,
            lynched=lynched,
        )
