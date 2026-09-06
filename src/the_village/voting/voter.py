from abc import abstractmethod

from the_village.core.bridge import SessionBridge
from the_village.core.state import GameState, VoteRecord


class _Voter:

    def __init__(
        self,
        state: GameState,
        bridge: SessionBridge,
        player_name: str,
    ):
        self._state = state
        self._bridge = bridge
        self._player_name = player_name

    async def cast(self) -> None:
        target = await self._cast()
        self._record_vote(target)

    @abstractmethod
    async def _cast(self) -> str | None:
        pass

    def _record_vote(self, target: str | None) -> None:
        record = VoteRecord(voter_name=self._player_name, target_name=target)
        self._state.current_day.votes.append(record)

    def _resolve_target(self, candidate: str | None) -> str | None:
        if not candidate or candidate == self._player_name:
            return None
        if candidate not in self._state.living_player_names():
            return None
        return candidate
