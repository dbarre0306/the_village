from crewai import Agent
from pydantic import BaseModel

from the_village.bridge import SessionBridge
from the_village.state import GameState, VoteRecord

from .ai_voter import _AiVoter
from .human_voter import _HumanVoter
from .voter import _Voter


class VoteOutcome(BaseModel):
    day_number: int
    votes: list[VoteRecord]
    tally: dict[str, int]
    lynched: str | None = None


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
        living_players = self._state.names_of_living_players()
        return {name: self._build_voter(name) for name in living_players}

    def _build_voter(self, player_name: str) -> _Voter:
        if self._state.is_human_player(player_name):
            return _HumanVoter(self._state, self._bridge, player_name)
        return _AiVoter(
            self._state,
            self._bridge,
            player_name,
            self._player_agents[player_name],
        )

    async def run(self) -> VoteOutcome:
        # roster.py always places the human first in state.players, and
        # names_of_living_players() preserves that order -- so the human's
        # ballot naturally comes up before any AI kickoff runs, with no
        # reordering needed to keep today's instant-ballot UX.
        for player_name in self._state.names_of_living_players():
            await self._voters[player_name].cast()
        return self._tally()

    def _tally(self) -> VoteOutcome:
        votes = self._state.current_day.votes
        tally: dict[str, int] = {}
        for vote in votes:
            if vote.target_name is not None:
                tally[vote.target_name] = tally.get(vote.target_name, 0) + 1

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
