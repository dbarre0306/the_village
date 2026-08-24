import logging

from .speaker import Speaker
from the_village.state import DiscussionMessage

logger = logging.getLogger(__name__)


class ReplyChain:

    def __init__(self, speakers: dict[str, Speaker]):
        self._speakers = speakers
        self._chain = frozenset()

    async def execute(self, message: DiscussionMessage) -> None:
        logger.debug(
            "ReplyChain.execute: instance=%s player_name=%s addressed_to=%s chain=%s",
            id(self),
            message.player_name,
            message.addressed_to,
            self._chain,
        )
        if message.addressed_to is None:
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
