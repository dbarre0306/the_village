import logging
from typing import Final

from .speaker import _Speaker
from the_village.core.state import DiscussionMessage

logger = logging.getLogger(__name__)

MAX_REPLY_CHAIN_LENGTH: Final = 3


class _ReplyChain:

    def __init__(self, speakers: dict[str, _Speaker]):
        self._speakers = speakers
        self._chain = frozenset()

    async def execute(self, message: DiscussionMessage) -> None:
        logger.debug(
            "_ReplyChain.execute: instance=%s player_name=%s addressed_to=%s chain=%s",
            id(self),
            message.player_name,
            message.addressed_to,
            self._chain,
        )
        if message.addressed_to is None:
            return

        if len(self._chain) >= MAX_REPLY_CHAIN_LENGTH:
            return

        # only allowed to speak once in a chain of replies
        target = message.addressed_to
        if target in self._chain:
            return

        speaker = self._speakers[target]
        reply = await speaker.speak(addressed_by=message)
        if reply is None:
            return

        self._chain = self._chain | {target}

        if reply.addressed_to is not None:
            await self.execute(reply)
