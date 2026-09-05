import logging

from crewai import Agent

from the_village.bridge import SessionBridge
from the_village.discussion.ai_speaker import _AiSpeaker

from the_village.state import GameState

logger = logging.getLogger(__name__)


class _WerewolfSpeaker(_AiSpeaker):

    def _inner_prompt_instructions(self) -> str:
        parts = [
            "Your goal is to have a normal villager blamed for any killings.  "
            "Never tell anyone you are a werewolf. You want someone else blamed for the killing, "
            "so you actively steer suspicion toward other villagers — voicing "
            "doubts about their behavior, questioning their alibi (but only if they have given an alibi), "
            "or agreeing with and amplifying accusations "
            "others raise — all without revealing yourself or your packmate. Your deception is in tone, "
            "interpretation, and misdirection, never in fabricated evidence: you never invent facts, "
            "alibis, or claims about what another villager did, said, or how they've been behaving. Any "
            "suspicion you cast must be grounded in something that was actually said in the discussion, "
            "even if you're spinning an unfair interpretation of it. If another villager has responded "
            "with hostility, insults, or a flat refusal to engage instead of answering, that's a gift -- "
            "a free target who isn't you or your packmate -- and you make a point of piling onto them by "
            "name on your next turn, ahead of any other thread, framing their outburst as the most "
            "damning thing anyone has done. ",
        ]
        return "\n".join(parts)
