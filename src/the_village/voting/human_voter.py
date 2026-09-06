from the_village.core.bridge import FlowStatus, SessionBridge
from the_village.core.state import GameState

from .voter import _Voter


class _HumanVoter(_Voter):

    def __init__(
        self,
        state: GameState,
        bridge: SessionBridge,
        player_name: str,
    ):
        super().__init__(state, bridge, player_name)

    async def _cast(self) -> str | None:
        await self._bridge.outbox.put(FlowStatus.WAITING_FOR_VOTE)
        player_input = await self._bridge.wait_for_input()
        return self._resolve_target(player_input.text)
