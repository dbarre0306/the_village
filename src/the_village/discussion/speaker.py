from abc import abstractmethod
import logging

from crewai import Agent
from pydantic import BaseModel, Field

from the_village.bridge import SessionBridge
from the_village.state import DiscussionMessage, GameState, Player

logger = logging.getLogger(__name__)

DECLINED_TO_RESPOND = "[declined to respond]"


class _AddressResolution(BaseModel):
    addressed_to: str | None = Field(
        default=None,
        description=(
            "The name of the living villager the message is speaking directly "
            "to, if any — e.g. asking them a question or accusing them. Leave "
            "unset if the message isn't addressing anyone in particular."
        ),
    )


class _Speaker:

    def __init__(
        self,
        state: GameState,
        bridge: SessionBridge,
        player_name: str,
        analyst_agent: Agent,
    ):
        self._state = state
        self._bridge = bridge
        self._player_name = player_name
        self._analyst_agent = analyst_agent
        self._names_of_other_living_players = state.names_of_other_living_players(
            player_name
        )

    async def speak(
        self,
        addressed_by: DiscussionMessage | None,
    ) -> DiscussionMessage | None:
        message = await self._speak(addressed_by)
        if message is None:
            logger.debug(
                f"DiscussionRunner._speak: {self._player_name} had nothing to say"
            )
        else:
            self._log_message(message)
            await self._bridge.outbox.put(message)
        return message

    def _log_message(self, message: DiscussionMessage):
        logger.debug(
            "_Speaker.speak: instance=%s putting message=%s player_name=%s day=%s",
            id(self),
            id(message),
            message.player_name,
            self._state.day_number,
        )

    @abstractmethod
    async def _speak(
        self,
        addressed_by: DiscussionMessage | None,
    ) -> DiscussionMessage | None:
        pass

    def _record_message(self, text: str, addressed_to: str | None) -> DiscussionMessage:
        msg = DiscussionMessage(
            player_name=self._player_name,
            text=text,
            addressed_to=addressed_to,
        )
        # need this to be here due to chaining replies
        # TODO: is this true?
        self._state.current_day.discussion.append(msg)
        logger.debug(
            "_record_message: state=%s day=%s player_name=%s addressed_to=%s message=%r",
            id(self._state),
            self._state.day_number,
            self._player_name,
            addressed_to,
            text,
        )
        return msg

    def _resolve_target(self, candidate: str | None) -> str | None:
        if not candidate or candidate == self._player_name:
            return None
        if candidate not in self._state.names_of_living_players():
            return None
        return candidate
