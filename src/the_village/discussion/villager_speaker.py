import logging

from crewai import Agent

from the_village.bridge import SessionBridge
from the_village.discussion.ai_speaker import _AiSpeaker

from the_village.state import GameState

logger = logging.getLogger(__name__)


class _VillagerSpeaker(_AiSpeaker):

    def _inner_prompt_instructions(self) -> str:
        parts = [
            "You pay attention to who seems evasive, inconsistent, too "
            "eager to point fingers, or have said contradictory things. "
            "You're willing to voice suspicion, ask pointed questions, and "
            "press others for answers. You try to be fair and not jump to conclusions quickly. "
            "If anyone has responded with hostility, insults, or a flat refusal to engage instead of "
            "answering, you treat that as one of the most damning things a person can do in this game -- "
            "more suspicious than a shaky alibi -- and you make a point of calling them out by name on "
            "your next turn, ahead of other threads you might otherwise raise. ",
        ]
        return "\n".join(parts)
