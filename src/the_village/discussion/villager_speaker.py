import logging

from crewai import Agent

from the_village.bridge import SessionBridge
from the_village.discussion.ai_speaker import _AiSpeaker

from the_village.state import GameState

logger = logging.getLogger(__name__)


class _VillagerSpeaker(_AiSpeaker):

    def _inner_prompt_instructions(self) -> str:
        parts = [
            "You pay attention to who seems evasive, inconsistent, or too "
            "eager to point fingers, and you're willing to voice suspicion, ask pointed questions, and "
            "press others for answers. You try to be fair and not jump to conclusions quickly. ",
        ]
        return "\n".join(parts)
