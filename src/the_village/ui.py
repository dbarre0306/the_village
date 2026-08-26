import asyncio
import logging

import gradio as gr

from the_village.bridge import (
    FlowFailed,
    FlowStatus,
    PlayerInput,
    SessionBridge,
    run_flow,
)
from the_village.village_flow import VillageFlow
from the_village.state import WEEKDAYS, Day, DiscussionMessage, GameState
from the_village.voting import VoteOutcome, tally_votes

logger = logging.getLogger(__name__)

# Categorical palette (light, dark) — fixed hue order, validated for CVD-safe
# adjacent contrast. Speakers are assigned a slot by their fixed position in
# the roster, not by speaking order, so a name's color never changes.
SPEAKER_COLORS = [
    ("#2a78d6", "#3987e5"),  # blue
    ("#eb6834", "#d95926"),  # orange
    ("#0e9488", "#14b8a6"),  # teal
    ("#eda100", "#c98500"),  # yellow
    ("#e87ba4", "#d55181"),  # magenta
    ("#4c9a2a", "#6cbf3f"),  # green
    ("#4a3aa7", "#9085e9"),  # violet
    ("#e34948", "#e66767"),  # red
]

RESULT_SCREEN_CLASS = "result-screen"
DISCUSSION_TRANSCRIPT_CLASS = "discussion-transcript"
DISCUSSION_INPUT_ROW_CLASS = "discussion-input-row"
PINNED_BAR_CLASS = "pinned-bar"
CHIP_LIST_CLASS = "chip-list"
VILLAGER_CHIP_CLASS = "villager-chip"
TYPING_INDICATOR_CLASS = "typing-indicator"
BEGIN_DISCUSSION_BUTTON_CLASS = "begin-discussion-button"
DISCUSSION_TITLE_CLASS = "discussion-title"
DEATH_LINE_CLASS = "death-line"
VOTE_CANDIDATE_BUTTON_CLASS = "vote-candidate-button"
HISTORY_LOG_CLASS = "history-log"
NIGHT_STRIP_CLASS = "night-strip"
LIVE_DAY_CARD_CLASS = "live-day-card"

# Matches roster.py's fixed count of 6 sampled AI villagers -- the vote
# ballot pre-allocates this many button slots since Gradio's layout is
# fixed at build time and can't grow/shrink with who's still alive.
MAX_VOTE_CANDIDATES = 6

# (light, dark)
DEATH_COLOR = ("#c62828", "#ef5350")

# How long a speaker's "typing" placeholder stays up before their message is
# revealed. Paces the transcript to human reading speed instead of dumping
# each AI turn in all at once.
SPEAKER_THINKING_DELAY_SECONDS = 3.0


def _speaker_color_css() -> str:
    # Defined on :root (not scoped to the transcript) so the same
    # --speaker-N variables are available to the living/dead villager chip
    # lists, which live in separate DOM subtrees from the transcript.
    light_vars = "; ".join(
        f"--speaker-{i}: {light}" for i, (light, _dark) in enumerate(SPEAKER_COLORS)
    )
    dark_vars = "; ".join(
        f"--speaker-{i}: {dark}" for i, (_light, dark) in enumerate(SPEAKER_COLORS)
    )
    light, dark = DEATH_COLOR
    return f"""
    :root {{ {light_vars}; --death-color: {light}; }}
    @media (prefers-color-scheme: dark) {{
        :root {{ {dark_vars}; --death-color: {dark}; }}
    }}
    .dark {{ {dark_vars}; --death-color: {dark}; }}
    .{DISCUSSION_TRANSCRIPT_CLASS} .speaker-name {{ font-weight: 600; }}
    .{DEATH_LINE_CLASS} {{ color: var(--death-color); }}
    {_vote_button_color_css()}
    """


def _vote_button_color_css() -> str:
    # Each candidate button is relabeled to a different villager every
    # round, so its color has to travel with a CSS class keyed to that
    # villager's speaker slot rather than a fixed per-button color.
    rules = "\n".join(
        f".{VOTE_CANDIDATE_BUTTON_CLASS}.speaker-btn-{i} {{ "
        f"background: var(--speaker-{i}); border-color: var(--speaker-{i}); }}"
        for i in range(len(SPEAKER_COLORS))
    )
    return f"""
    .{VOTE_CANDIDATE_BUTTON_CLASS} {{ color: #fff; }}
    {rules}
    """


def _layout_css() -> str:
    return f"""
    /* Sticky positioning needs an unambiguous scrolling ancestor. Gradio's
       default layout lets the document/body scroll, and an in-between
       wrapper can silently break `position: sticky` depending on its
       overflow. Making the container itself the explicit scroll context
       guarantees the pinned bar always has a well-defined ancestor to
       stick to. */
    .gradio-container {{
        height: 100vh;
        overflow-y: auto;
    }}
    .{PINNED_BAR_CLASS} {{
        position: sticky;
        top: 0;
        z-index: 10;
        background: var(--body-background-fill);
        border-bottom: 1px solid var(--border-color-primary);
        padding-bottom: 8px;
        margin-bottom: 8px;
    }}
    .{CHIP_LIST_CLASS} {{
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
    }}
    .{VILLAGER_CHIP_CLASS} {{
        display: inline-block;
        padding: 2px 10px;
        border-radius: 999px;
        background: var(--background-fill-secondary);
        border: 1px solid var(--border-color-primary);
        font-size: 0.85em;
    }}
    .{VILLAGER_CHIP_CLASS}.dead {{
        text-decoration: line-through;
        opacity: 0.6;
    }}
    .{TYPING_INDICATOR_CLASS} {{
        display: inline-flex;
        gap: 4px;
        vertical-align: middle;
    }}
    .{TYPING_INDICATOR_CLASS} span {{
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: var(--body-text-color-subdued);
        opacity: 0.4;
        animation: typing-bounce 1s infinite ease-in-out;
    }}
    .{TYPING_INDICATOR_CLASS} span:nth-child(2) {{ animation-delay: 0.15s; }}
    .{TYPING_INDICATOR_CLASS} span:nth-child(3) {{ animation-delay: 0.3s; }}
    @keyframes typing-bounce {{
        0%, 80%, 100% {{ opacity: 0.4; transform: scale(0.8); }}
        40% {{ opacity: 1; transform: scale(1); }}
    }}
    /* Discussion turns already show their own "typing" placeholder while
       streaming, so Gradio's generic pulsing border on components mid-update
       is redundant noise -- suppress it. */
    .generating {{
        border: none !important;
        animation: none !important;
    }}
    .{BEGIN_DISCUSSION_BUTTON_CLASS} {{
        background: var(--lantern, #d99a3d);
        border-color: var(--lantern, #d99a3d);
        color: var(--ink, #2b2420);
        font-weight: 600;
    }}
    .{BEGIN_DISCUSSION_BUTTON_CLASS}:hover {{
        background: #c98a30;
        border-color: #c98a30;
    }}
    """


def _chronicle_css() -> str:
    # The Village is a chronicle, not a chat app: a fixed parchment/dusk
    # palette (not theme-aware -- a storybook page doesn't flip to dark mode)
    # carries a day/night rhythm through the whole content area. Deliberately
    # scoped away from .pinned-bar/.chip-list/.villager-chip -- the pinned
    # header keeps its existing stock look untouched.
    return f"""
    @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Source+Serif+4:ital,wght@0,400;0,600;1,400&family=IBM+Plex+Sans:wght@400;500;600&display=swap');

    .{RESULT_SCREEN_CLASS} {{
        --ink: #2b2420;
        --parchment: #ede2cc;
        --parchment-edge: #d3c29a;
        --dusk: #232338;
        --ember: #b8452f;
        --moonlight: #c9d6e8;
        --lantern: #d99a3d;
        --death-color: var(--ember);
    }}

    .{HISTORY_LOG_CLASS},
    .{DISCUSSION_TITLE_CLASS},
    .{DISCUSSION_TRANSCRIPT_CLASS},
    .{LIVE_DAY_CARD_CLASS},
    .{NIGHT_STRIP_CLASS} {{
        font-family: 'Source Serif 4', Georgia, serif;
        color: var(--ink);
        font-size: 1.02em;
        line-height: 1.55;
    }}
    /* Gradio's own prose CSS sets color directly on <p>/<h1-6>/<li>/<strong>
       (light, for its default dark theme), which beats inheriting --ink
       from the class above -- a directly-targeted element always wins over
       an inherited value, regardless of selector specificity. Force it back
       explicitly wherever a speaker span doesn't already carry its own
       color. Scoped to the two parchment containers as a whole (not just
       the transcript/title classes within them) so it also covers plain
       Markdown status lines like discussion_status and vote_status that
       carry no elem_classes of their own -- h5 is excluded here since
       history_log's own h5 rule further down needs to win instead.
       Deliberately spelled out one selector per tag rather than
       `:is(p, h1, ...)`: Gradio's CSS scoping rewrites custom stylesheets by
       prefixing every selector (and every argument *inside* a `:is()`
       individually) with `.gradio-container... .contain`, which multiplies
       an `:is()` group's effective specificity far past a plain descendant
       selector like `.night-strip strong` below -- breaking the deliberate
       tie-break that lets that rule win for the death line's own text. */
    .{HISTORY_LOG_CLASS} p, .{HISTORY_LOG_CLASS} h1, .{HISTORY_LOG_CLASS} h2,
    .{HISTORY_LOG_CLASS} h3, .{HISTORY_LOG_CLASS} h4, .{HISTORY_LOG_CLASS} h6,
    .{HISTORY_LOG_CLASS} li, .{HISTORY_LOG_CLASS} strong,
    .{LIVE_DAY_CARD_CLASS} p, .{LIVE_DAY_CARD_CLASS} h1, .{LIVE_DAY_CARD_CLASS} h2,
    .{LIVE_DAY_CARD_CLASS} h3, .{LIVE_DAY_CARD_CLASS} h4, .{LIVE_DAY_CARD_CLASS} h5,
    .{LIVE_DAY_CARD_CLASS} h6, .{LIVE_DAY_CARD_CLASS} li, .{LIVE_DAY_CARD_CLASS} strong {{
        color: var(--ink);
    }}

    /* Gradio gives a Markdown component's elem_classes to both its outer
       wrapper AND the inner .prose div holding the rendered content -- every
       rule below that sets background/border/padding/margin is scoped with
       :not(.prose) so it lands on the outer box exactly once instead of
       nesting two copies of the same box inside each other. */

    /* The permanent record: every finished day, newest first, as a
       continuous parchment scroll broken by chapter headers and
       night-strip transitions. Hidden entirely until the first day has a
       chapter to show. */
    .{HISTORY_LOG_CLASS}:not(.prose) {{
        background: var(--parchment);
        border: 1px solid var(--parchment-edge);
        border-radius: 10px;
        padding: 4px 28px 20px;
        margin-bottom: 20px;
    }}
    .{HISTORY_LOG_CLASS}:not(.prose):not(:has(h3)) {{
        display: none;
    }}

    .{HISTORY_LOG_CLASS} h3,
    .{DISCUSSION_TITLE_CLASS} {{
        font-family: 'Fraunces', Georgia, serif;
        font-weight: 600;
        font-size: 1.5em;
        letter-spacing: 0.01em;
    }}
    .{HISTORY_LOG_CLASS} h3 {{ margin: 24px 0 10px; }}
    .{DISCUSSION_TITLE_CLASS}:not(.prose) {{ margin: 24px 0 10px; }}
    .{HISTORY_LOG_CLASS} h3:first-child {{ margin-top: 8px; }}

    .{HISTORY_LOG_CLASS} h5 {{
        font-family: 'IBM Plex Sans', sans-serif;
        font-size: 0.78em;
        font-weight: 600;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--ember);
        margin: 20px 0 8px;
        padding-top: 14px;
        border-top: 1px dashed var(--parchment-edge);
    }}

    /* The night strip: the game's real heartbeat (something dies every
       night) as a recurring, full-width dark banner between day chapters. */
    .{NIGHT_STRIP_CLASS} {{
        display: block;
        font-family: 'IBM Plex Sans', sans-serif;
        font-weight: 500;
        font-size: 0.92em;
        text-align: center;
        color: var(--moonlight);
        background:
            radial-gradient(1px 1px at 12% 35%, rgba(201,214,232,0.65), transparent 60%),
            radial-gradient(1px 1px at 30% 75%, rgba(201,214,232,0.5), transparent 60%),
            radial-gradient(1.5px 1.5px at 55% 25%, rgba(201,214,232,0.7), transparent 60%),
            radial-gradient(1px 1px at 78% 65%, rgba(201,214,232,0.5), transparent 60%),
            radial-gradient(1.5px 1.5px at 90% 40%, rgba(201,214,232,0.65), transparent 60%),
            var(--dusk);
        border-top: 1px solid rgba(217, 154, 61, 0.4);
        border-bottom: 1px solid rgba(217, 154, 61, 0.4);
        border-radius: 6px;
        padding: 12px 18px;
        margin: 20px 0;
    }}
    /* Explicit color here (not just inherited from .night-strip) since the
       broader ink-forcing rule above targets <strong> directly, at equal
       specificity, and would otherwise win by simply coming first. */
    .{NIGHT_STRIP_CLASS} strong {{ font-weight: 500; color: var(--moonlight); }}
    .{NIGHT_STRIP_CLASS} .{DEATH_LINE_CLASS} {{ color: var(--lantern); }}
    .{NIGHT_STRIP_CLASS}::before,
    .{NIGHT_STRIP_CLASS}::after {{
        content: '\\2726';
        color: var(--lantern);
        font-size: 0.85em;
        margin: 0 10px;
        opacity: 0.8;
    }}

    /* The current round: a single fixed card near the top of the page --
       titled with the day of the week, gated behind one "Begin" click that
       reveals the night's death and the day's discussion in place. Finished
       days pile up *below* it in history_log, so this card never moves and
       there's never a hunt for where to click next. */
    .{LIVE_DAY_CARD_CLASS} {{
        background: var(--parchment);
        border: 1px solid var(--parchment-edge);
        border-left: 4px solid var(--lantern);
        border-radius: 10px;
        padding: 4px 28px 24px;
        margin: 4px 0 20px;
        box-shadow: 0 0 28px rgba(217, 154, 61, 0.18);
    }}
    .{LIVE_DAY_CARD_CLASS} .{DISCUSSION_TITLE_CLASS}:first-child {{ margin-top: 8px; }}
    .{LIVE_DAY_CARD_CLASS} .{DISCUSSION_TRANSCRIPT_CLASS}:empty {{ display: none; }}

    .{DISCUSSION_TRANSCRIPT_CLASS} p {{ margin: 0 0 14px; }}
    """


def _autoscroll_js() -> str:
    # The live day card is pinned near the top of the page (see build_app)
    # and finished days pile up *below* it, newest first -- so scrolling to
    # the bottom of the page on new content would scroll past the live
    # round into old history. Instead, follow the live card's own bottom
    # edge as it grows, the way a chat app follows its newest message.
    #
    # Discussion turns and the vote tally both stream in as separate yields,
    # so we can't hook a single event's completion to know when to scroll.
    # A MutationObserver reacts to every content change instead, regardless
    # of how many times the Markdown gets updated.
    return f"""
    (() => {{
        const attach = () => {{
            const liveCard = document.querySelector(".{LIVE_DAY_CARD_CLASS}");
            if (!liveCard) {{
                setTimeout(attach, 200);
                return;
            }}
            const followLiveCard = () => {{
                liveCard.scrollIntoView({{block: "end"}});
            }};
            new MutationObserver(followLiveCard).observe(liveCard, {{
                childList: true,
                subtree: true,
                characterData: true,
            }});
        }};
        attach();
    }})();
    """


def _autofocus_js() -> str:
    # Gradio doesn't keep the row in the DOM with display:none while
    # hidden -- it's conditionally mounted, so a fresh <div> (and a fresh
    # <textarea> inside it) is created each time it's the player's turn.
    # Watching one node's attributes for a visibility flip never fires;
    # instead watch the document for that node being inserted at all.
    return f"""
    (() => {{
        const focusedRows = new WeakSet();
        const tryFocus = () => {{
            const row = document.querySelector(".{DISCUSSION_INPUT_ROW_CLASS}");
            if (!row || focusedRows.has(row) || getComputedStyle(row).display === "none") {{
                return;
            }}
            const textarea = row.querySelector("textarea");
            if (textarea) {{
                textarea.focus();
                focusedRows.add(row);
            }}
        }};
        new MutationObserver(tryFocus).observe(document.body, {{
            childList: true,
            subtree: true,
        }});
        tryFocus();
    }})();
    """


def _speaker_color_index(name: str, state: GameState) -> int:
    roster_names = [player.name for player in state.players]
    if name not in roster_names:
        return 0
    return roster_names.index(name) % len(SPEAKER_COLORS)


DEATH_MESSAGE_TEMPLATES = [
    "{name} was found dead, torn apart by a werewolf attack.",
    "The pack struck again: {name} was found mauled to death.",
    "{name} was found dead, ravaged by wolf jaws in the dark of night.",
    "A werewolf attack claimed {name} overnight; their body was found at dawn.",
    "{name} never made it to morning, savaged by a werewolf under cover of darkness.",
    "{name} didn't survive the night — a werewolf victim.",
]


def _dead_days(state: GameState) -> list[Day]:
    return [day for day in state.days if day.player_found_dead]


def _format_death_line(day: Day, index: int) -> str:
    return (
        "<strong>"
        f"{WEEKDAYS[(day.day_number - 1) % 7]} morning: "
        f'<span class="{DEATH_LINE_CLASS}">'
        + DEATH_MESSAGE_TEMPLATES[index % len(DEATH_MESSAGE_TEMPLATES)].format(
            name=day.player_found_dead
        )
        + "</span></strong>"
    )


def _format_night_line(day: Day, index: int) -> str:
    # An inline element (not a <div>) so it stays safe to embed inside a
    # gr.Markdown value alongside real markdown syntax elsewhere in the same
    # string -- block-level raw HTML mixed with markdown is parser-fragile,
    # inline HTML nested in a markdown paragraph (as the speaker-name spans
    # already do) is not. `display: block` in CSS still gives it the full-width
    # banner look.
    return f'<span class="{NIGHT_STRIP_CLASS}">{_format_death_line(day, index)}</span>'


def format_latest_death_announcement(state: GameState) -> str:
    dead_days = _dead_days(state)
    return _format_night_line(dead_days[-1], len(dead_days) - 1)


def format_deaths_panel(state: GameState) -> str:
    dead_days = [day for day in state.days if day.player_found_dead]
    if not dead_days:
        return f'<div class="{CHIP_LIST_CLASS}">No one has been killed yet.</div>'
    chips = "".join(
        f'<span class="{VILLAGER_CHIP_CLASS} dead" '
        f'style="color: var(--speaker-{_speaker_color_index(day.player_found_dead, state)})">'
        f"{day.player_found_dead}</span>"
        for day in dead_days
    )
    return f'<div class="{CHIP_LIST_CLASS}">{chips}</div>'


def format_lynched_panel(state: GameState) -> str:
    lynched_days = [day for day in state.days if day.player_lynched]
    if not lynched_days:
        return f'<div class="{CHIP_LIST_CLASS}">No one has been lynched yet.</div>'
    chips = "".join(
        f'<span class="{VILLAGER_CHIP_CLASS} dead" '
        f'style="color: var(--speaker-{_speaker_color_index(day.player_lynched, state)})">'
        f"{day.player_lynched}</span>"
        for day in lynched_days
    )
    return f'<div class="{CHIP_LIST_CLASS}">{chips}</div>'


def _vote_candidate_names(state: GameState) -> list[str]:
    return [
        player.name
        for player in state.players
        if player.is_alive and player.name != state.user_player_name
    ]


def _vote_button_updates(state: GameState) -> list:
    names = _vote_candidate_names(state)
    updates = []
    for i in range(MAX_VOTE_CANDIDATES):
        if i < len(names):
            name = names[i]
            color_index = _speaker_color_index(name, state)
            updates.append(
                gr.Button(
                    value=name,
                    visible=True,
                    elem_classes=[
                        VOTE_CANDIDATE_BUTTON_CLASS,
                        f"speaker-btn-{color_index}",
                    ],
                )
            )
        else:
            updates.append(gr.Button(visible=False))
    return updates


def format_alive_panel(state: GameState) -> str:
    alive = [player for player in state.players if player.is_alive]
    if not alive:
        return f'<div class="{CHIP_LIST_CLASS}">No one is left.</div>'
    chips = "".join(
        f'<span class="{VILLAGER_CHIP_CLASS}" '
        f'style="color: var(--speaker-{_speaker_color_index(player.name, state)})">'
        f'{player.name}{" (me)" if player.name == state.user_player_name else ""}'
        f"</span>"
        for player in alive
    )
    return f'<div class="{CHIP_LIST_CLASS}">{chips}</div>'


def _colored_name(name: str, state: GameState) -> str:
    return f'<span style="color: var(--speaker-{_speaker_color_index(name, state)})">{name}</span>'


def _speaker_name_span(name: str, state: GameState) -> str:
    return (
        f'<span class="speaker-name" '
        f'style="color: var(--speaker-{_speaker_color_index(name, state)})">'
        f"{name}:</span>"
    )


def _format_transcript_lines(
    messages: list[DiscussionMessage], state: GameState
) -> list[str]:
    return [f"{_speaker_name_span(m.player_name, state)} {m.text}" for m in messages]


def format_discussion_transcript(
    state: GameState,
    limit: int | None = None,
    pending_speaker: str | None = None,
    waiting: bool = False,
) -> str:
    # `limit` caps how many of state.current_day.discussion's messages are
    # shown. DiscussionRunner appends straight to this same live GameState
    # and doesn't wait for the UI to consume each outbox item before
    # continuing, so by the time a given item is rendered, state may already
    # hold messages the caller hasn't paced onto screen yet -- rendering
    # "all of state" (or "all but the literal last one") would leak those
    # ahead-of-pace messages into this render instead of just this item's.
    # `pending_speaker` additionally appends a "typing" placeholder for that
    # speaker after the limited messages, so the reveal can be paced.
    # `waiting` appends that same placeholder with no name attached, for the
    # dead time before anyone's turn -- and thus their identity -- is known.
    all_messages = state.current_day.discussion
    messages = all_messages if limit is None else all_messages[:limit]
    lines = _format_transcript_lines(messages, state)
    typing_indicator = f'<span class="{TYPING_INDICATOR_CLASS}"><span></span><span></span><span></span></span>'
    if pending_speaker is not None:
        lines.append(f"{_speaker_name_span(pending_speaker, state)} {typing_indicator}")
    elif waiting:
        lines.append(typing_indicator)
    if not lines:
        return ""
    return "\n\n".join(lines)


def format_completed_round_history(state: GameState) -> str:
    # state.current_day (the live, still-in-progress round) is excluded --
    # its discussion/vote widgets render that content live elsewhere on the
    # page. Every prior day is fully resolved, so it's folded in here once
    # and for all rather than staying in the live widgets, which get reused
    # (and reset) for each new round.
    #
    # Rendered newest-first (reversed) so a freshly completed day lands
    # immediately below the live card -- the live card is a fixed position
    # near the top of the page, so this is what makes each new day appear
    # to stack "above" the last one instead of appending to the bottom of
    # an ever-growing scroll.
    dead_day_index = {id(day): index for index, day in enumerate(_dead_days(state))}
    completed_days = state.days[:-1]
    blocks = []
    for day in reversed(completed_days):
        weekday = WEEKDAYS[(day.day_number - 1) % 7]
        section = []
        if day.player_found_dead:
            section.append(_format_night_line(day, dead_day_index[id(day)]))
        section.append(f"### {weekday}")
        transcript_lines = _format_transcript_lines(day.discussion, state)
        if transcript_lines:
            section.append("\n\n".join(transcript_lines))
        if day.votes or day.player_lynched:
            outcome = VoteOutcome(
                day_number=day.day_number,
                votes=day.votes,
                tally=tally_votes(day.votes),
                lynched=day.player_lynched,
            )
            section.append("##### The Village Votes")
            section.append(format_vote_result(state, outcome))
        blocks.append("\n\n".join(section))
    return "\n\n".join(blocks)


async def _stream_bridge(bridge: SessionBridge, state: GameState):
    """Drains bridge.outbox, yielding a UI-update tuple per item, until a
    status tells the caller to stop and hand control back to the player."""
    # DiscussionRunner appends straight to this same live GameState and
    # doesn't wait for the UI to consume each outbox item before continuing
    # (an address-chain reply can resolve in well under
    # SPEAKER_THINKING_DELAY_SECONDS), so it can race ahead -- appending,
    # even recording, further messages before this call starts or while an
    # earlier item's placeholder is still being paced. bridge's own reveal
    # counter (not state's current size, which the runner can move out from
    # under us at any point) is what each render stays pinned to.
    try:
        while True:
            item = await bridge.outbox.get()
            logger.debug("_stream_bridge: bridge=%s got item=%r", id(bridge), item)
            if isinstance(item, FlowFailed):
                raise gr.Error("Something went wrong, please try again.")
            if isinstance(item, DiscussionMessage):
                # Pace AI turns to reading speed with a "typing" placeholder;
                # the player's own message (already visible to them as they
                # typed it) shows immediately with no delay.
                if item.player_name != state.user_player_name:
                    pending_transcript = format_discussion_transcript(
                        state,
                        limit=bridge.revealed_discussion_messages,
                        pending_speaker=item.player_name,
                    )
                    yield (
                        bridge,
                        pending_transcript,
                        gr.update(value=""),
                        gr.update(visible=False),
                        gr.update(),
                        gr.update(),
                        gr.update(),
                    )
                    await asyncio.sleep(SPEAKER_THINKING_DELAY_SECONDS)
                bridge.revealed_discussion_messages += 1
                transcript = format_discussion_transcript(
                    state, limit=bridge.revealed_discussion_messages
                )
                yield (
                    bridge,
                    transcript,
                    gr.update(value=""),
                    gr.update(visible=False),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )
            elif (
                item == FlowStatus.WAITING_FOR_TURN
                or item == FlowStatus.WAITING_FOR_ANSWER
            ):
                yield (
                    bridge,
                    gr.update(),
                    gr.update(value=""),
                    gr.update(visible=True),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )
                return
            elif item == FlowStatus.DISCUSSION_COMPLETE:
                yield (
                    bridge,
                    gr.update(),
                    gr.update(),
                    gr.update(visible=False),
                    gr.update(
                        visible=True, value="The Moderator has ended the discussion."
                    ),
                    gr.update(),
                    gr.update(),
                )
                return
    except gr.Error:
        raise
    except Exception as exc:
        logger.exception("Discussion turn failed")
        raise gr.Error("Something went wrong, please try again.") from exc


async def begin_discussion(bridge: SessionBridge, state: GameState):
    if not bridge.resolve_input(PlayerInput()):
        return  # already resolved (e.g. double-click) -- no-op
    # format_discussion_transcript renders state.current_day.discussion only
    # (each completed day's transcript already lives in history_log instead)
    # -- reset the reveal counter so a new round's pacing starts from that
    # day's first message rather than continuing the previous round's count.
    bridge.revealed_discussion_messages = 0
    yield (
        bridge,
        format_discussion_transcript(
            state, limit=bridge.revealed_discussion_messages, waiting=True
        ),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(visible=False),
        # The panel's weekday title is already visible (set by start_game or
        # cast_player_vote) -- clicking Begin only needs to reveal this
        # round's death and let the discussion below it start streaming.
        gr.update(value=format_latest_death_announcement(state), visible=True),
    )
    async for update in _stream_bridge(bridge, state):
        yield update


async def send_discussion_turn(bridge: SessionBridge, state: GameState, message: str):
    if not message.strip():
        yield (gr.skip(),) * 7
        return
    if not bridge.resolve_input(PlayerInput(text=message.strip())):
        return
    yield (
        bridge,
        gr.update(),
        gr.update(),
        gr.update(visible=False),
        gr.update(),
        gr.update(),
        gr.update(),
    )
    async for update in _stream_bridge(bridge, state):
        yield update


async def pass_discussion_turn(bridge: SessionBridge, state: GameState):
    if not bridge.resolve_input(PlayerInput(text=None)):
        return
    yield (
        bridge,
        gr.update(),
        gr.update(),
        gr.update(visible=False),
        gr.update(),
        gr.update(),
        gr.update(),
    )
    async for update in _stream_bridge(bridge, state):
        yield update


async def begin_voting(bridge: SessionBridge, state: GameState):
    if not bridge.resolve_input(PlayerInput()):
        return  # already resolved (e.g. double-click) -- no-op
    yield (
        bridge,
        gr.update(visible=False),  # begin_voting_button
        gr.update(),  # vote_button_row (shown once WAITING_FOR_VOTE arrives)
        *([gr.update()] * MAX_VOTE_CANDIDATES),
        gr.update(),  # vote_status
    )
    try:
        while True:
            item = await bridge.outbox.get()
            if isinstance(item, FlowFailed):
                raise gr.Error("Something went wrong, please try again.")
            if item == FlowStatus.WAITING_FOR_VOTE:
                yield (
                    bridge,
                    gr.update(),
                    gr.update(visible=True),
                    *_vote_button_updates(state),
                    gr.update(visible=False),
                )
                return
            elif isinstance(item, VoteOutcome):
                # Not reachable today (night.py always keeps the human alive
                # through night one), but if Voting.run() ever completes
                # without a _HumanVoter ever pausing here, show the outcome
                # instead of silently discarding it.
                yield (
                    bridge,
                    gr.update(),
                    gr.update(),
                    *([gr.update()] * MAX_VOTE_CANDIDATES),
                    gr.update(value=format_vote_result(state, item), visible=True),
                )
            elif item == FlowStatus.VOTING_COMPLETE:
                # Nothing left for this handler to show -- the vote is
                # already fully decided. Return instead of looping forever
                # on an empty queue.
                return
    except gr.Error:
        raise
    except Exception as exc:
        logger.exception("Voting turn failed")
        raise gr.Error("Something went wrong, please try again.") from exc


def format_vote_result(state: GameState, outcome: VoteOutcome) -> str:
    lines = [
        (
            f"{_colored_name(record.voter_name, state)} voted for "
            f"{_colored_name(record.target_name, state)}."
            if record.target_name is not None
            else f"{_colored_name(record.voter_name, state)} abstained."
        )
        for record in outcome.votes
    ]
    lines.append("")
    if outcome.tally:
        lines.append(
            "  ·  ".join(
                f"{_colored_name(name, state)}: {count}"
                for name, count in sorted(
                    outcome.tally.items(), key=lambda kv: (-kv[1], kv[0])
                )
            )
        )
        lines.append("")
    if outcome.lynched is not None:
        lines.append(
            f"**{_colored_name(outcome.lynched, state)} was lynched by the village.**"
        )
    elif outcome.tally:
        lines.append("**The vote was tied — no one was lynched.**")
    else:
        lines.append("**No one voted to lynch anyone — no one was lynched.**")
    return "\n\n".join(lines)


async def cast_player_vote(bridge: SessionBridge, state: GameState, target: str | None):
    if not bridge.resolve_input(PlayerInput(text=target)):
        return
    # Hide the ballot the instant the player votes, before the AI kickoffs
    # that Voting.run() drives inside the background Flow task resolve.
    yield (
        gr.update(visible=False),
        gr.update(value="Tallying the votes…", visible=True),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
    )
    try:
        while True:
            item = await bridge.outbox.get()
            if isinstance(item, FlowFailed):
                raise gr.Error("Something went wrong, please try again.")
            if isinstance(item, VoteOutcome):
                yield (
                    gr.update(),
                    gr.update(value=format_vote_result(state, item), visible=True),
                    format_alive_panel(state),
                    format_lynched_panel(state),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )
            elif item == FlowStatus.VOTING_COMPLETE:
                # VillageFlow.run_next_night routes straight into the next
                # night and re-arms announce_death, so the very next outbox
                # item is the following morning's death announcement (a bare
                # str) -- the same shape start_game() waits on for night
                # one. It's only consumed here (to unblock the flow's own
                # outbox draining); it isn't displayed until the player
                # clicks Begin for the new round -- see begin_discussion.
                death = await bridge.outbox.get()
                if isinstance(death, FlowFailed):
                    raise gr.Error("Something went wrong, please try again.")
                weekday = WEEKDAYS[(state.day_number - 1) % 7]
                yield (
                    gr.update(),
                    gr.update(visible=False),
                    format_alive_panel(state),
                    gr.update(),
                    format_deaths_panel(state),
                    gr.update(visible=True),
                    gr.update(value=f"### {weekday}", visible=True),
                    gr.update(visible=False),
                    gr.update(value=format_completed_round_history(state)),
                    gr.update(value=""),
                    gr.update(visible=False),
                )
                return
    except gr.Error:
        raise
    except Exception as exc:
        logger.exception("Vote casting failed")
        raise gr.Error("Something went wrong, please try again.") from exc


async def cast_player_abstain(bridge: SessionBridge, state: GameState):
    async for update in cast_player_vote(bridge, state, None):
        yield update


async def start_game(player_name: str):
    if not player_name or not player_name.strip():
        raise gr.Error("Please enter your name.")

    bridge = SessionBridge()
    village_flow = VillageFlow(bridge=bridge)
    bridge.task = asyncio.create_task(
        run_flow(
            village_flow.kickoff_async(
                inputs={"user_player_name": player_name.strip()}
            ),
            bridge,
        )
    )

    item = await bridge.outbox.get()
    if isinstance(item, FlowFailed):
        raise gr.Error("Something went wrong, please try again.")

    state = village_flow.state
    weekday = WEEKDAYS[(state.day_number - 1) % 7]
    yield (
        gr.update(visible=False),
        gr.update(visible=True),
        format_deaths_panel(state),
        format_alive_panel(state),
        format_lynched_panel(state),
        gr.update(value=f"### {weekday}", visible=True),
        state,
        bridge,
    )


def build_app() -> gr.Blocks:
    with gr.Blocks(title="The Village") as demo:
        game_state = gr.State()
        session_bridge = gr.State()

        with gr.Column(visible=True) as start_screen:
            name_input = gr.Textbox(label="Your first name")
            start_button = gr.Button("Start Game")

        with gr.Column(
            visible=False, elem_classes=[RESULT_SCREEN_CLASS]
        ) as result_screen:
            with gr.Row(elem_classes=[PINNED_BAR_CLASS]):
                with gr.Column():
                    gr.Markdown("### Living Villagers")
                    alive_panel = gr.Markdown()
                with gr.Column():
                    gr.Markdown("### Killed by Werewolves")
                    deaths_panel = gr.Markdown()
                with gr.Column():
                    gr.Markdown("### Lynched by the Village")
                    lynched_panel = gr.Markdown()
            with gr.Column():
                # The current round: one fixed card, always in this same
                # spot. Its title is set once per round (by start_game or
                # cast_player_vote) and stays visible; a single "Begin"
                # click reveals that night's death and the day's discussion
                # in place. When the round ends, its content is folded into
                # history_log (below it, newest day first) and this card
                # resets for the next day -- so a new day always appears to
                # stack "above" the last one without ever moving itself.
                with gr.Column(elem_classes=[LIVE_DAY_CARD_CLASS]):
                    discussion_title = gr.Markdown(
                        visible=False, elem_classes=[DISCUSSION_TITLE_CLASS]
                    )
                    begin_discussion_button = gr.Button(
                        "Begin", elem_classes=[BEGIN_DISCUSSION_BUTTON_CLASS]
                    )
                    panel_death_line = gr.Markdown(visible=False)
                    discussion_transcript = gr.Markdown(
                        elem_classes=[DISCUSSION_TRANSCRIPT_CLASS]
                    )
                    with gr.Row(
                        visible=False, elem_classes=[DISCUSSION_INPUT_ROW_CLASS]
                    ) as discussion_input_row:
                        discussion_textbox = gr.Textbox(label="Say something", scale=3)
                        with gr.Column(scale=1):
                            send_button = gr.Button("Post Message")
                            pass_button = gr.Button("I have nothing to say")
                    discussion_status = gr.Markdown(visible=False)
                    begin_voting_button = gr.Button(
                        "Begin Voting",
                        elem_classes=[BEGIN_DISCUSSION_BUTTON_CLASS],
                        visible=False,
                    )
                    with gr.Row(visible=False) as vote_button_row:
                        candidate_buttons = [
                            gr.Button(visible=False) for _ in range(MAX_VOTE_CANDIDATES)
                        ]
                        abstain_button = gr.Button("Abstain")
                    vote_status = gr.Markdown(visible=False)
                # Every day once it's finished, newest first -- see
                # format_completed_round_history.
                history_log = gr.Markdown(elem_classes=[HISTORY_LOG_CLASS])

        start_button.click(
            fn=start_game,
            inputs=[name_input],
            outputs=[
                start_screen,
                result_screen,
                deaths_panel,
                alive_panel,
                lynched_panel,
                discussion_title,
                game_state,
                session_bridge,
            ],
            concurrency_limit=None,
        )

        discussion_outputs = [
            session_bridge,
            discussion_transcript,
            discussion_textbox,
            discussion_input_row,
            discussion_status,
            begin_discussion_button,
            panel_death_line,
        ]

        # SessionBridge.resolve_input() is the per-session no-pending-future
        # guard that replaces the old shared "discussion_turn" concurrency_id
        # -- a double-click (or overlapping submit) becomes a no-op instead
        # of racing on shared state, without serializing unrelated sessions.
        begin_discussion_button.click(
            fn=begin_discussion,
            inputs=[session_bridge, game_state],
            outputs=discussion_outputs,
            concurrency_limit=None,
        )

        send_button.click(
            fn=send_discussion_turn,
            inputs=[session_bridge, game_state, discussion_textbox],
            outputs=discussion_outputs,
            concurrency_limit=None,
        )

        discussion_textbox.submit(
            fn=send_discussion_turn,
            inputs=[session_bridge, game_state, discussion_textbox],
            outputs=discussion_outputs,
            concurrency_limit=None,
        )

        pass_button.click(
            fn=pass_discussion_turn,
            inputs=[session_bridge, game_state],
            outputs=discussion_outputs,
            concurrency_limit=None,
        )

        begin_voting_button.click(
            fn=begin_voting,
            inputs=[session_bridge, game_state],
            outputs=[
                session_bridge,
                begin_voting_button,
                vote_button_row,
                *candidate_buttons,
                vote_status,
            ],
            concurrency_limit=None,
        )

        discussion_status.change(
            fn=lambda status_text: gr.update(visible=bool(status_text)),
            inputs=[discussion_status],
            outputs=[begin_voting_button],
        )

        vote_outputs = [
            vote_button_row,
            vote_status,
            alive_panel,
            lynched_panel,
            deaths_panel,
            begin_discussion_button,
            discussion_title,
            discussion_status,
            history_log,
            discussion_transcript,
            panel_death_line,
        ]

        # SessionBridge.resolve_input()'s no-pending-future guard (see
        # begin_discussion_button.click above) is what protects a
        # double-click here now -- cast_player_vote/cast_player_abstain no
        # longer call vote-casting logic directly, they resolve the bridge
        # and let VillageFlow.run_voting drive the AI kickoffs.
        for button in candidate_buttons:
            button.click(
                fn=cast_player_vote,
                inputs=[session_bridge, game_state, button],
                outputs=vote_outputs,
                concurrency_limit=None,
            )

        abstain_button.click(
            fn=cast_player_abstain,
            inputs=[session_bridge, game_state],
            outputs=vote_outputs,
            concurrency_limit=None,
        )

    return demo


def main():
    build_app().launch(
        css=_speaker_color_css() + _layout_css() + _chronicle_css(),
        js=_autoscroll_js() + _autofocus_js(),
    )


if __name__ == "__main__":
    main()
