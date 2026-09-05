import asyncio
import logging
from typing import NamedTuple

import gradio as gr

from the_village.bridge import (
    FlowFailed,
    FlowStatus,
    GameOverResult,
    PlayerInput,
    SessionBridge,
    run_flow,
)
from the_village.village_flow import VillageFlow
from the_village.roster import NUMBER_OF_PLAYERS
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
DISCUSSION_BUTTON_COLUMN_CLASS = "discussion-button-column"
PINNED_BAR_CLASS = "pinned-bar"
CHIP_LIST_CLASS = "chip-list"
VILLAGER_CHIP_CLASS = "villager-chip"
TYPING_INDICATOR_CLASS = "typing-indicator"
BEGIN_DISCUSSION_BUTTON_CLASS = "begin-discussion-button"
DISCUSSION_TITLE_CLASS = "discussion-title"
DEATH_LINE_CLASS = "death-line"
VOTE_CANDIDATE_BUTTON_CLASS = "vote-candidate-button"
HISTORY_LOG_CLASS = "history-log"
DAY_PANEL_CLASS = "day-panel"
NIGHT_STRIP_CLASS = "night-strip"
LIVE_DAY_CARD_CLASS = "live-day-card"
PANEL_DEATH_LINE_CLASS = "panel-death-line"
MODERATOR_NOTICE_CLASS = "moderator-notice"
BALLOT_QUESTION_CLASS = "ballot-question"
GAME_OVER_STATUS_CLASS = "game-over-status"
GAME_OVER_HEADLINE_CLASS = "game-over-headline"
GAME_OVER_BUTTON_ROW_CLASS = "game-over-button-row"
PLAY_AGAIN_BUTTON_CLASS = "play-again-button"
EXIT_BUTTON_CLASS = "exit-button"

# Matches roster.py's fixed count of sampled AI villagers -- the vote
# ballot pre-allocates this many button slots since Gradio's layout is
# fixed at build time and can't grow/shrink with who's still alive.
MAX_VOTE_CANDIDATES = NUMBER_OF_PLAYERS

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
    .{DISCUSSION_BUTTON_COLUMN_CLASS} {{ --layout-gap: 4px; }}
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
    .{GAME_OVER_BUTTON_ROW_CLASS} {{
        justify-content: center;
    }}
    .{PLAY_AGAIN_BUTTON_CLASS}, .{EXIT_BUTTON_CLASS} {{
        flex: none;
        width: auto;
        min-width: 140px;
    }}
    .{PLAY_AGAIN_BUTTON_CLASS} {{
        background: #2e8b45;
        border-color: #2e8b45;
        color: #fff;
    }}
    .{PLAY_AGAIN_BUTTON_CLASS}:hover {{
        background: #26753a;
        border-color: #26753a;
    }}
    .{EXIT_BUTTON_CLASS} {{
        background: #2f6fb3;
        border-color: #2f6fb3;
        color: #fff;
    }}
    .{EXIT_BUTTON_CLASS}:hover {{
        background: #275d96;
        border-color: #275d96;
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
        --amethyst: #7a4988;
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
       carry no elem_classes of their own -- h5 is excluded in both
       containers since their shared h5 rule further down (covering both
       history_log's archived "The Village Votes" heading and the same
       label live in discussion_status) needs to win instead.
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
    .{LIVE_DAY_CARD_CLASS} h3, .{LIVE_DAY_CARD_CLASS} h4,
    .{LIVE_DAY_CARD_CLASS} h6, .{LIVE_DAY_CARD_CLASS} li, .{LIVE_DAY_CARD_CLASS} strong,
    .{GAME_OVER_STATUS_CLASS} p, .{GAME_OVER_STATUS_CLASS} h1, .{GAME_OVER_STATUS_CLASS} h2,
    .{GAME_OVER_STATUS_CLASS} h3, .{GAME_OVER_STATUS_CLASS} h4,
    .{GAME_OVER_STATUS_CLASS} h6, .{GAME_OVER_STATUS_CLASS} li,
    .{GAME_OVER_STATUS_CLASS} strong {{
        color: var(--ink);
    }}

    /* Gradio gives a Markdown component's elem_classes to both its outer
       wrapper AND the inner .prose div holding the rendered content -- every
       rule below that sets background/border/padding/margin is scoped with
       :not(.prose) so it lands on the outer box exactly once instead of
       nesting two copies of the same box inside each other. */

    /* The permanent record: every finished day, oldest first, as its own
       parchment panel with a chapter header and night-strip transitions.
       .history-log itself is just a transparent layout container -- hidden
       entirely until the first day has a panel to show. */
    .{HISTORY_LOG_CLASS}:not(.prose):not(:has(.{DAY_PANEL_CLASS})) {{
        display: none;
    }}
    .{DAY_PANEL_CLASS} {{
        background: var(--parchment);
        border: 1px solid var(--parchment-edge);
        border-radius: 10px;
        padding: 4px 28px 20px;
        margin-bottom: 20px;
    }}

    /* Gradio's own `.gradio-container-X .prose h1..h5` rule (two classes
       deep) sets font-size/margin on every rendered heading and otherwise
       beats a plain `.history-log h3` selector on specificity regardless of
       source order -- our override has to be qualified with `.prose` too
       (real DOM: each Markdown component's elem_class lands on both its
       outer wrapper and its inner .prose div, so `.prose.history-log`
       matches the same element) to actually win the cascade. */
    .prose.{HISTORY_LOG_CLASS} h3,
    .prose.{DISCUSSION_TITLE_CLASS} h3 {{
        font-family: 'Fraunces', Georgia, serif;
        font-weight: 600;
        font-size: 1.9em;
        letter-spacing: 0.01em;
        margin: 24px 0 10px;
    }}
    .prose.{HISTORY_LOG_CLASS} h3:first-child,
    .prose.{DISCUSSION_TITLE_CLASS} h3:first-child {{ margin-top: 8px; }}
    /* The night banner sits in its own <p> (Markdown wraps the bare
       night-strip span in a paragraph) directly under the day heading --
       without this override the paragraph's own margin plus the banner's
       default 20px top margin stack into a gap far wider than intended. */
    .{HISTORY_LOG_CLASS} h3 + p:has(> .{NIGHT_STRIP_CLASS}) {{ margin-top: 0; }}
    .{HISTORY_LOG_CLASS} h3 + p > .{NIGHT_STRIP_CLASS} {{ margin-top: 8px; }}
    /* The live day card's death line (panel_death_line) is a separate
       component from the day title, sitting one flex item away in
       live-day-card's column -- the column's flex `gap` (16px) stacks with
       the title's own margin-bottom and the banner's default top margin,
       producing a much wider gap than the history-log equivalent above.
       Pull it back up by exactly that stacked amount and match the same
       tight top margin used there. */
    .{PANEL_DEATH_LINE_CLASS}:not(.prose) {{ margin-top: -24px; }}
    .{PANEL_DEATH_LINE_CLASS} .{NIGHT_STRIP_CLASS} {{ margin-top: 8px; }}

    .{HISTORY_LOG_CLASS} h5,
    .{LIVE_DAY_CARD_CLASS} h5,
    .{GAME_OVER_STATUS_CLASS} h5 {{
        font-family: 'IBM Plex Sans', sans-serif;
        font-size: 1.3em;
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

    /* The current round: a single fixed card at the bottom of the page --
       titled with the day of the week. Every day, including day one, is
       gated behind a "Begin" click that reveals the night's death and the
       day's discussion in place; once that round's voting completes, the
       card resets straight into this same Begin-gated state for the next
       day, no click needed to advance the round itself. Finished days pile
       up *above* it in history_log, so this card never moves and there's
       never a hunt for where to click next. */
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

    /* Separates the moderator's "discussion stopped" line from the
       werewolf-guess prompt/"The Village Votes" label that follows it, in
       discussion_status. */
    .{LIVE_DAY_CARD_CLASS} hr {{
        border: none;
        border-top: 2px dotted var(--parchment-edge);
        margin: 16px 0;
    }}

    /* Makes the moderator's interruption read as a break in the scene,
       not just another line of prose. */
    .{MODERATOR_NOTICE_CLASS} {{
        display: block;
        font-family: 'Fraunces', Georgia, serif;
        font-weight: 600;
        font-size: 1.3em;
        color: var(--ember);
    }}

    /* The ballot question: the moment the player has to point a finger at
       someone. Larger and italic so it reads as a dramatic beat, not just
       another line of body text. */
    .{BALLOT_QUESTION_CLASS} {{
        display: block;
        font-family: 'Fraunces', Georgia, serif;
        font-weight: 600;
        font-style: italic;
        font-size: 1.5em;
        color: var(--amethyst);
        text-align: center;
        margin: 4px 0;
    }}

    .{DISCUSSION_TRANSCRIPT_CLASS} p {{ margin: 0 0 14px; }}

    /* The game's final beat: bigger and more ornamented than the ballot
       question it echoes, since this is the one line the whole story has
       been building toward. Color forks on who actually won. */
    .{GAME_OVER_HEADLINE_CLASS} {{
        display: block;
        font-family: 'Fraunces', Georgia, serif;
        font-weight: 600;
        font-size: 2.3em;
        letter-spacing: 0.01em;
        text-align: center;
        margin: 8px 0 20px;
    }}
    .{GAME_OVER_HEADLINE_CLASS}.villagers-won {{ color: var(--lantern); }}
    .{GAME_OVER_HEADLINE_CLASS}.werewolves-won {{ color: var(--ember); }}
    .{GAME_OVER_HEADLINE_CLASS}::before,
    .{GAME_OVER_HEADLINE_CLASS}::after {{
        content: '\\2726';
        font-size: 0.55em;
        margin: 0 14px;
        opacity: 0.8;
        vertical-align: middle;
    }}
    """


def _autoscroll_js() -> str:
    # The live day card sits at the bottom of the page (see build_app), with
    # finished days piled up *above* it, each in its own panel oldest first
    # -- so scrolling to a fixed spot on new content wouldn't track the live
    # round as the page grows. Instead, follow the live card's own bottom
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


def _game_over_scroll_js() -> str:
    # game_over_panel starts visible=False, and like discussion_input_row
    # (see _autofocus_js) it's conditionally mounted rather than just
    # display:none'd -- so there's no fixed node to grab up front. Watch the
    # document for its markdown filling in and jump to it, the same
    # watch-and-catch approach _autofocus_js uses for the reappearing
    # textarea.
    return f"""
    (() => {{
        const scrolledPanels = new WeakSet();
        const tryScroll = () => {{
            const status = document.querySelector(".{GAME_OVER_STATUS_CLASS}");
            if (!status || scrolledPanels.has(status) || !status.textContent.trim()) {{
                return;
            }}
            scrolledPanels.add(status);
            // Scroll the whole panel (status text + Play Again button) into
            // view, not just the status markdown -- scrolling to the
            // markdown's own bottom edge left the button below the fold.
            const panel = status.closest(".{DAY_PANEL_CLASS}") ?? status;
            panel.scrollIntoView({{block: "end"}});
        }};
        new MutationObserver(tryScroll).observe(document.body, {{
            childList: true,
            subtree: true,
            characterData: true,
        }});
        tryScroll();
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


def _discussion_complete_notice(state: GameState) -> str:
    # discussion_status.change() is what auto-triggers start_voting (see
    # build_app), and Gradio only fires .change() when the component's
    # value actually changes. The visible text is otherwise a hardcoded
    # constant, so a later round reusing the exact same string as an
    # earlier one would never register as a change -- silently hanging the
    # flow forever at run_discussion's post-discussion wait_for_input(). A
    # hidden per-day marker keeps the value distinct round to round without
    # altering what's rendered.
    #
    # A dead human player never gets a ballot (Voting._build_voters only
    # builds voters for living players -- see voting.py), so asking them
    # "The werewolf is among you. Who do you think it is?" is misleading; skip
    # the question entirely once they're out of the game.
    is_human_alive = state.user_player_name in state.living_player_names()
    ballot_question = (
        f'<div class="{BALLOT_QUESTION_CLASS}">'
        "The werewolf is among you. Who do you think it is?"
        "</div>\n\n"
        if is_human_alive
        else ""
    )
    return (
        f'<div class="{MODERATOR_NOTICE_CLASS}">'
        "The moderator has stopped the discussion."
        "</div>\n\n"
        "---\n\n"
        f"{ballot_question}"
        f'<span style="display:none">day {state.day_number}</span>'
    )


def _voting_results_notice(state: GameState) -> str:
    # Swaps in for _discussion_complete_notice's ballot question once the
    # outcome is known -- the vote is over, so "The werewolf is among you. Who
    # do you think it is?" no longer applies and would otherwise sit there
    # unchanged (discussion_status is not touched again after this) through
    # the tally and past the final lynch result.
    #
    # Rendered as the same h5 used for this label in the archived history
    # panel (see format_completed_round_history) -- picks up that rule's
    # uppercase/ember styling and dashed top-border, so no separate "---"
    # divider is needed here (one would double up with the h5's own border).
    return (
        f'<div class="{MODERATOR_NOTICE_CLASS}">'
        "The moderator has stopped the discussion."
        "</div>\n\n"
        "##### The Village Votes\n\n"
        f'<span style="display:none">day {state.day_number}</span>'
    )


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


def format_deaths_panel(state: GameState, include_current_day_death: bool = True) -> str:
    # The current day's kill lands in state (and thus here) the instant
    # WereWolfPack picks a victim -- well before the player has clicked
    # "Begin" to reveal it (see begin_discussion). include_current_day_death
    # lets the pre-Begin renders (start_game, _next_day_setup) keep hiding
    # it until that click.
    dead_days = [
        day
        for day in state.days
        if day.player_found_dead
        and (include_current_day_death or day is not state.current_day)
    ]
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


def format_alive_panel(state: GameState, include_current_day_death: bool = True) -> str:
    # Player.is_alive flips to False the instant WereWolfPack picks a
    # victim, same as player_found_dead above -- mirror
    # include_current_day_death's hiding here so the panel doesn't shrink by
    # one before the death itself is revealed.
    hidden_victim = (
        None if include_current_day_death else state.current_day.player_found_dead
    )
    alive = [
        player
        for player in state.players
        if player.is_alive or player.name == hidden_victim
    ]
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


def format_completed_round_history(
    state: GameState, include_current_day: bool = False
) -> str:
    # state.current_day (the live, still-in-progress round) is excluded by
    # default -- its discussion/vote widgets render that content live
    # elsewhere on the page. Every prior day is fully resolved, so it's
    # folded in here once and for all rather than staying in the live
    # widgets, which get reused (and reset) for each new round.
    # include_current_day=True overrides that for the game-over panel,
    # whose final round never advances into a new Day (see
    # VillageFlow.check_winner_after_lynching) and so would otherwise never
    # get folded into the permanent record at all.
    #
    # Rendered oldest-first (chronological), each day in its own panel --
    # newly completed days append at the bottom of the stack rather than
    # appearing directly under the live card.
    dead_day_index = {id(day): index for index, day in enumerate(_dead_days(state))}
    completed_days = state.days if include_current_day else state.days[:-1]
    blocks = []
    for day in completed_days:
        weekday = WEEKDAYS[(day.day_number - 1) % 7]
        section = []
        section.append(f"### {weekday}")
        if day.player_found_dead:
            section.append(_format_night_line(day, dead_day_index[id(day)]))
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
            # The live discussion_status notice announcing this is folded
            # into the permanent record too, rather than vanishing once the
            # round resets into the next day's Begin-gated panel. No extra
            # "---" divider is needed before the heading below -- the
            # history-log h5 rule already gives "The Village Votes" its own
            # dashed top-border, so adding one here would double it up.
            section.append(
                f'<div class="{MODERATOR_NOTICE_CLASS}">'
                "The moderator has stopped the discussion."
                "</div>"
            )
            section.append("##### The Village Votes")
            section.append(format_vote_result(state, outcome))
        # Blank lines around the div tags are required so the markdown
        # renderer treats them as a raw HTML block (per CommonMark's type-6
        # HTML block rule) and resumes parsing the day's own markdown
        # (headers, paragraphs) normally in between, instead of swallowing
        # the whole panel as unparsed HTML.
        panel_body = "\n\n".join(section)
        blocks.append(f'<div class="{DAY_PANEL_CLASS}">\n\n{panel_body}\n\n</div>')
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
            # Shown for the entire time this call is blocked below not
            # knowing what's coming next -- whether that's an AI turn still
            # being generated (unpredictable latency, unlike the fixed
            # SPEAKER_THINKING_DELAY_SECONDS pacing once a message has
            # already arrived) or the flow resolving to the player's turn.
            # Only relevant when the next item isn't already queued -- e.g.
            # right after revealing the player's own already-known message,
            # where the next status can be sitting on the outbox before this
            # loop even comes back around, and flashing "someone is about to
            # speak" for a wait that never actually happens is stale, not
            # informative.
            if bridge.outbox.empty():
                yield (
                    bridge,
                    format_discussion_transcript(
                        state, limit=bridge.revealed_discussion_messages, waiting=True
                    ),
                    gr.update(value=""),
                    gr.update(visible=False),
                    gr.update(),
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
            item = await bridge.outbox.get()
            logger.debug("_stream_bridge: bridge=%s got item=%r", id(bridge), item)
            if isinstance(item, FlowFailed):
                raise gr.Error("Something went wrong, please try again.")
            if isinstance(item, GameOverResult):
                yield (
                    bridge,
                    format_discussion_transcript(
                        state, limit=bridge.revealed_discussion_messages
                    ),
                    gr.update(),
                    gr.update(visible=False),
                    gr.update(visible=False),
                    gr.update(visible=False),
                    gr.update(visible=False),
                    gr.update(
                        value=format_completed_round_history(
                            state, include_current_day=True
                        )
                    ),
                    gr.update(visible=True),
                    gr.update(value=format_game_over(item, state)),
                    # The live day card's own title never advances into a new
                    # Day when a night kill ends the game (see
                    # _next_day_setup's GameOverResult short-circuit) -- it's
                    # still sitting there visible from this round's Begin
                    # click. Left alone, live-day-card renders as its own
                    # (empty) panel below the archived history entry above,
                    # right next to the real results panel.
                    gr.update(visible=False),
                    # And the card's own container needs hiding too, not
                    # just its title -- with every child hidden/empty it
                    # still renders as its own collapsed, parchment-backed
                    # sliver of a panel.
                    gr.update(visible=False),
                    # begin_discussion's own first yield already revealed
                    # this round's death here -- nothing left to update.
                    gr.update(),
                    gr.update(),
                )
                return
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
                        gr.update(),
                        gr.update(),
                        gr.update(),
                        gr.update(),
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
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )
            elif (
                item == FlowStatus.WAITING_FOR_TURN
                or item == FlowStatus.WAITING_FOR_ANSWER
            ):
                # Whose turn it is is now known -- it's the player's, about
                # to type into the input row this same yield opens. Render
                # the transcript without a waiting/pending placeholder so the
                # loop-top "someone is about to speak" indicator (queued for
                # the dead time before anyone's turn was known) doesn't
                # linger alongside it.
                yield (
                    bridge,
                    format_discussion_transcript(
                        state, limit=bridge.revealed_discussion_messages
                    ),
                    gr.update(value=""),
                    gr.update(visible=True),
                    gr.update(),
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
                return
            elif item == FlowStatus.DISCUSSION_COMPLETE:
                yield (
                    bridge,
                    # Explicit re-render (not gr.update()) so the loop-top
                    # waiting placeholder above doesn't linger on screen now
                    # that discussion has ended.
                    format_discussion_transcript(
                        state, limit=bridge.revealed_discussion_messages
                    ),
                    gr.update(),
                    gr.update(visible=False),
                    gr.update(
                        visible=True,
                        value=_discussion_complete_notice(state),
                    ),
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
    # A new round's discussion-complete notice needs to be able to trigger
    # start_voting again -- see SessionBridge.voting_started.
    bridge.voting_started = False
    yield (
        bridge,
        format_discussion_transcript(
            state, limit=bridge.revealed_discussion_messages, waiting=True
        ),
        gr.update(
            label=f"{state.user_player_name}, please say something "
            "(unless you have nothing to say)"
        ),
        gr.update(),
        gr.update(),
        gr.update(visible=False),
        # The panel's weekday title is already visible (set by start_game or
        # _next_day_setup) -- clicking Begin only needs to reveal this
        # round's death and let the discussion below it start streaming.
        # This is now day one's flow reused as-is for every day: nothing
        # here needs to know or care which day it's being called for.
        gr.update(value=format_latest_death_announcement(state), visible=True),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        # The reveal this whole click exists for: the night's kill, hidden
        # from these two panels until now by _next_day_setup/start_game's
        # include_current_day_death=False.
        format_alive_panel(state),
        format_deaths_panel(state),
    )
    async for update in _stream_bridge(bridge, state):
        yield update


async def send_discussion_turn(bridge: SessionBridge, state: GameState, message: str):
    if not message.strip():
        yield (gr.skip(),) * 14
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
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
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
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
    )
    async for update in _stream_bridge(bridge, state):
        yield update


async def start_voting(bridge: SessionBridge, state: GameState):
    # Auto-triggered by discussion_status.change once discussion ends --
    # there's no "Begin Voting" button to gate this anymore, so this only
    # needs to resolve the flow's own wait_for_input() and start listening
    # for the ballot to open.
    #
    # discussion_status.change() fires a second time later in the same
    # round, when this function's own VoteOutcome branch rewrites
    # discussion_status to the "The Village Votes" label -- that spurious
    # re-entry must return here, before resolve_input(), or it can resolve
    # a *later* pause point (the next day's announce_death) that has
    # nothing to do with it. See SessionBridge.voting_started.
    if bridge.voting_started:
        # A generator invocation that yields nothing at all (a bare return
        # before any yield) appears to leave Gradio's bound outputs in a
        # blank/pending state rather than untouched -- yielding an explicit
        # no-op tuple, even though nothing here actually changes, avoids
        # that. gr.skip() (not gr.update()) is what makes it a true no-op:
        # gr.update() still ships a real, empty update for every one of
        # these components, and since this whole branch only runs because
        # this same event's *own* real update (the discussion_status swap
        # above) just fired again, that redundant update lands in the
        # frontend concurrently with the real one still streaming in --
        # visibly flickering every bound component and briefly remounting
        # the ones mid-transition (e.g. vote_status/discussion_status right
        # before their real hide arrives), leaving a blank panel behind.
        # gr.skip() removes these components from the payload entirely, so
        # this call touches nothing. Matches this event's outputs list:
        # session_bridge, vote_button_row, *candidate_buttons, vote_status,
        # discussion_status, alive_panel, lynched_panel, deaths_panel,
        # begin_discussion_button, discussion_title, history_log,
        # discussion_transcript, panel_death_line, game_over_panel,
        # game_over_status, live_day_card.
        yield (gr.skip(),) * (15 + MAX_VOTE_CANDIDATES)
        return
    bridge.voting_started = True
    if not bridge.resolve_input(PlayerInput()):
        return  # already resolved (e.g. double-fire) -- no-op
    try:
        while True:
            item = await bridge.outbox.get()
            if isinstance(item, FlowFailed):
                raise gr.Error("Something went wrong, please try again.")
            if item == FlowStatus.WAITING_FOR_VOTE:
                yield (
                    bridge,
                    gr.update(visible=True),
                    *_vote_button_updates(state),
                    gr.update(visible=False),
                    gr.update(),
                    gr.update(),
                    gr.update(),
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
                return
            elif isinstance(item, VoteOutcome):
                # Happens once the human dies on an earlier night: no
                # _HumanVoter ever pauses on WAITING_FOR_VOTE, so Voting.run()
                # completes entirely from AI votes and this handler -- not
                # cast_player_vote -- is the one draining the outcome. Show
                # it instead of silently discarding it. discussion_status
                # also needs the swap to _voting_results_notice here (it's
                # the same swap cast_player_vote does in the human-alive
                # path) since nothing else touches it in this path.
                yield (
                    bridge,
                    gr.update(),
                    *([gr.update()] * MAX_VOTE_CANDIDATES),
                    gr.update(value=format_vote_result(state, item), visible=True),
                    gr.update(value=_voting_results_notice(state)),
                    gr.update(),
                    gr.update(),
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
            elif item == FlowStatus.VOTING_COMPLETE:
                # cast_player_vote is what normally advances into the next
                # day's Begin-gated panel on VOTING_COMPLETE, but it never
                # runs in the human-is-dead path above -- do it here instead,
                # or the round has no way forward. See _next_day_setup.
                # _next_day_setup then blocks on the werewolf pack's
                # kill-selection LLM call (via bridge.outbox.get()), which
                # can take several seconds with nothing yielded in between --
                # yield a no-op heartbeat first so the frontend has a fresh
                # update to hold onto rather than sitting on a stale pending
                # state that long. gr.skip() (not gr.update()) keeps it a
                # true no-op -- see the matching comment on the
                # bridge.voting_started guard above for why gr.update()
                # here visibly flickers/blanks components that aren't
                # actually changing.
                yield (bridge,) + (gr.skip(),) * 20
                next_step = await _next_day_setup(bridge, state)
                if isinstance(next_step, GameOverResult):
                    yield (
                        bridge,
                        gr.update(),
                        *([gr.update()] * MAX_VOTE_CANDIDATES),
                        gr.update(visible=False),
                        gr.update(visible=False),
                        format_alive_panel(state),
                        format_lynched_panel(state),
                        format_deaths_panel(state),
                        gr.update(visible=False),
                        gr.update(visible=False),
                        gr.update(
                            value=format_completed_round_history(
                                state, include_current_day=True
                            )
                        ),
                        gr.update(value=""),
                        gr.update(visible=False),
                        gr.update(visible=True),
                        gr.update(value=format_game_over(next_step, state)),
                        # The live day card never advances into a new Day
                        # when this lynch just ended the game -- left alone
                        # it renders as its own (collapsed but visible, still
                        # parchment-backed) empty panel next to the real
                        # results panel below it. See the matching branch in
                        # _stream_bridge for the night-kill-ends-the-game
                        # equivalent.
                        gr.update(visible=False),
                    )
                    return
                yield (
                    bridge,
                    gr.update(),
                    *([gr.update()] * MAX_VOTE_CANDIDATES),
                    gr.update(visible=False),
                    gr.update(visible=False),
                    next_step.alive_panel,
                    format_lynched_panel(state),
                    next_step.deaths_panel,
                    # Gradio unmounts a hidden component entirely (see the
                    # comment on discussion_input_row in _autofocus_js), so
                    # its label has to be resent here, not just `visible`,
                    # or the remounted button comes back blank.
                    gr.update(value="Begin", visible=True),
                    next_step.discussion_title,
                    next_step.history_log,
                    gr.update(value=""),
                    gr.update(visible=False),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )
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


def format_game_over(result: GameOverResult, state: GameState) -> str:
    villagers_won = result.winner == "villagers"
    headline = "The Villagers Win!" if villagers_won else "The Werewolves Win!"
    outcome_class = "villagers-won" if villagers_won else "werewolves-won"

    def chips(names: list[str]) -> str:
        return "".join(
            f'<span class="{VILLAGER_CHIP_CLASS}" '
            f'style="color: var(--speaker-{_speaker_color_index(name, state)})">'
            f'{name}{" (me)" if name == state.user_player_name else ""}</span>'
            for name in names
        )

    villager_names = [player.name for player in state.players if player.is_not_werewolf]
    return (
        f'<div class="{GAME_OVER_HEADLINE_CLASS} {outcome_class}">{headline}</div>\n\n'
        f"##### The Werewolves\n\n"
        f'<div class="{CHIP_LIST_CLASS}">{chips(result.werewolf_names)}</div>\n\n'
        f"##### The Villagers\n\n"
        f'<div class="{CHIP_LIST_CLASS}">{chips(villager_names)}</div>'
    )


class _NextDayPanel(NamedTuple):
    alive_panel: str
    deaths_panel: str
    discussion_title: dict
    history_log: dict


async def _next_day_setup(
    bridge: SessionBridge, state: GameState
) -> _NextDayPanel | GameOverResult:
    # VillageFlow.run_next_night routes straight into the next night and
    # re-arms announce_death, so the very next outbox item is normally the
    # following morning's death announcement (a bare str) -- the same shape
    # start_game waits on for night one. But if check_winner_after_lynching
    # decided the game just ended instead, run_next_night never runs, and
    # this is a GameOverResult instead. Draining it here (without resolving
    # anything) lets the flow keep running in the background while
    # announce_death stays paused at its own wait_for_input() -- resolving
    # that pause is begin_discussion_button.click's job, same as day one, so
    # every day gates its death reveal and discussion behind the same
    # "Begin" click.
    item = await bridge.outbox.get()
    if isinstance(item, FlowFailed):
        raise gr.Error("Something went wrong, please try again.")
    if isinstance(item, GameOverResult):
        return item
    weekday = WEEKDAYS[(state.day_number - 1) % 7]
    return _NextDayPanel(
        # This new day's kill is already in state (see the module-level
        # comment on format_deaths_panel) but must stay hidden until the
        # player clicks "Begin" for it -- begin_discussion is what reveals
        # it, both in panel_death_line and by re-rendering these two panels.
        alive_panel=format_alive_panel(state, include_current_day_death=False),
        deaths_panel=format_deaths_panel(state, include_current_day_death=False),
        discussion_title=gr.update(value=f"### {weekday}", visible=True),
        history_log=gr.update(value=format_completed_round_history(state)),
    )


async def cast_player_vote(bridge: SessionBridge, state: GameState, target: str | None):
    if not bridge.resolve_input(PlayerInput(text=target)):
        return
    # Hide the ballot the instant the player votes, before the AI kickoffs
    # that Voting.run() drives inside the background Flow task resolve. The
    # ballot question is answered the moment the player picks -- swap it for
    # the results label here too, rather than waiting for the outcome to
    # arrive later.
    yield (
        gr.update(visible=False),
        gr.update(value="Tallying the votes…", visible=True),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(value=_voting_results_notice(state)),
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
                    gr.update(value=_voting_results_notice(state)),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )
            elif item == FlowStatus.VOTING_COMPLETE:
                # The round auto-advances into the next day's panel as soon
                # as the outcome is known, in the same Begin-gated state day
                # one starts in -- see _next_day_setup. _next_day_setup then
                # blocks on the werewolf pack's kill-selection LLM call
                # (via bridge.outbox.get()), which can take several seconds
                # with nothing yielded in between -- yield a no-op heartbeat
                # first so the frontend has a fresh update to hold onto
                # rather than sitting on a stale pending state that long.
                # gr.skip() (not gr.update()) keeps it a true no-op -- see
                # the matching comment on start_voting's bridge.voting_started
                # guard for why gr.update() here visibly
                # flickers/blanks components that aren't actually changing.
                yield (gr.skip(),) * 14
                next_step = await _next_day_setup(bridge, state)
                if isinstance(next_step, GameOverResult):
                    yield (
                        gr.update(visible=False),
                        gr.update(visible=False),
                        gr.update(),
                        gr.update(),
                        gr.update(),
                        gr.update(visible=False),
                        gr.update(visible=False),
                        gr.update(visible=False),
                        gr.update(
                            value=format_completed_round_history(
                                state, include_current_day=True
                            )
                        ),
                        gr.update(value=""),
                        gr.update(visible=False),
                        gr.update(visible=True),
                        gr.update(value=format_game_over(next_step, state)),
                        # The live day card never advances into a new Day
                        # when this vote just ended the game -- left alone
                        # it renders as its own (collapsed but visible,
                        # still parchment-backed) empty panel next to the
                        # real results panel below it.
                        gr.update(visible=False),
                    )
                    return
                yield (
                    gr.update(),
                    gr.update(visible=False),
                    next_step.alive_panel,
                    gr.update(),
                    next_step.deaths_panel,
                    # Gradio unmounts a hidden component entirely (see the
                    # comment on discussion_input_row in _autofocus_js), so
                    # its label has to be resent here, not just `visible`,
                    # or the remounted button comes back blank.
                    gr.update(value="Begin", visible=True),
                    next_step.discussion_title,
                    gr.update(visible=False),
                    next_step.history_log,
                    gr.update(value=""),
                    gr.update(visible=False),
                    gr.update(),
                    gr.update(),
                    gr.update(),
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
        # Night one's kill is already in state by this point (see the
        # module-level comment on format_deaths_panel) but must stay hidden
        # until the player clicks "Begin" -- begin_discussion is what
        # reveals it, both in panel_death_line and by re-rendering these two
        # panels.
        format_deaths_panel(state, include_current_day_death=False),
        format_alive_panel(state, include_current_day_death=False),
        format_lynched_panel(state),
        gr.update(value=f"### {weekday}", visible=True),
        state,
        bridge,
        gr.update(visible=False),
        gr.update(value=""),
        gr.update(value=""),
        gr.update(value=""),
        gr.update(visible=False),
        gr.update(value="Begin", visible=True),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        # A previous game (via Play Again) may have hidden the live day
        # card on GameOverResult -- always show it again for a fresh game.
        gr.update(visible=True),
    )


async def play_again(state: GameState):
    async for update in start_game(state.user_player_name):
        yield update


def exit_to_home():
    return gr.update(visible=True), gr.update(visible=False)


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
                # Every day once it's finished, each in its own panel,
                # oldest first -- see format_completed_round_history. Placed
                # above the live card so the whole page reads in
                # chronological order top to bottom (Monday, Tuesday, ...,
                # then whatever day is currently in progress).
                history_log = gr.Markdown(elem_classes=[HISTORY_LOG_CLASS])
                # The current round: one fixed card, always in this same
                # spot at the bottom of the stack. Its title is set once per
                # round (by start_game or _next_day_setup) and stays
                # visible. Every day gates its death reveal and discussion
                # behind the same "Begin" click -- as soon as the previous
                # day's voting completes, this card resets into that same
                # Begin-gated state automatically (see _next_day_setup), no
                # click needed to advance the round itself. When a round
                # ends, its content is folded into history_log (just above
                # it, as its own panel appended at the end) -- so the live
                # card stays put at the bottom while finished days pile up
                # above it, oldest first.
                with gr.Column(elem_classes=[LIVE_DAY_CARD_CLASS]) as live_day_card:
                    discussion_title = gr.Markdown(
                        visible=False, elem_classes=[DISCUSSION_TITLE_CLASS]
                    )
                    begin_discussion_button = gr.Button(
                        "Begin", elem_classes=[BEGIN_DISCUSSION_BUTTON_CLASS]
                    )
                    panel_death_line = gr.Markdown(
                        visible=False, elem_classes=[PANEL_DEATH_LINE_CLASS]
                    )
                    discussion_transcript = gr.Markdown(
                        elem_classes=[DISCUSSION_TRANSCRIPT_CLASS]
                    )
                    with gr.Row(
                        visible=False, elem_classes=[DISCUSSION_INPUT_ROW_CLASS]
                    ) as discussion_input_row:
                        discussion_textbox = gr.Textbox(
                            label="Say something (unless you have nothing to say)",
                            scale=3,
                        )
                        with gr.Column(
                            scale=1, elem_classes=[DISCUSSION_BUTTON_COLUMN_CLASS]
                        ):
                            send_button = gr.Button("Post Message")
                            pass_button = gr.Button("I have nothing to say")
                    discussion_status = gr.Markdown(visible=False)
                    with gr.Row(visible=False) as vote_button_row:
                        candidate_buttons = [
                            gr.Button(visible=False) for _ in range(MAX_VOTE_CANDIDATES)
                        ]
                        abstain_button = gr.Button("Abstain")
                    vote_status = gr.Markdown(visible=False)
                with gr.Column(
                    visible=False, elem_classes=[DAY_PANEL_CLASS]
                ) as game_over_panel:
                    game_over_status = gr.Markdown(elem_classes=[GAME_OVER_STATUS_CLASS])
                    with gr.Row(elem_classes=[GAME_OVER_BUTTON_ROW_CLASS]):
                        play_again_button = gr.Button(
                            "Play Again", elem_classes=[PLAY_AGAIN_BUTTON_CLASS]
                        )
                        exit_button = gr.Button("Exit", elem_classes=[EXIT_BUTTON_CLASS])

        start_game_outputs = [
            start_screen,
            result_screen,
            deaths_panel,
            alive_panel,
            lynched_panel,
            discussion_title,
            game_state,
            session_bridge,
            game_over_panel,
            game_over_status,
            history_log,
            discussion_transcript,
            panel_death_line,
            begin_discussion_button,
            discussion_status,
            vote_button_row,
            vote_status,
            live_day_card,
        ]

        start_button.click(
            fn=start_game,
            inputs=[name_input],
            outputs=start_game_outputs,
            concurrency_limit=None,
        )

        play_again_button.click(
            fn=play_again,
            inputs=[game_state],
            outputs=start_game_outputs,
            concurrency_limit=None,
        )

        exit_button.click(
            fn=exit_to_home,
            inputs=None,
            outputs=[start_screen, result_screen],
        )

        discussion_outputs = [
            session_bridge,
            discussion_transcript,
            discussion_textbox,
            discussion_input_row,
            discussion_status,
            begin_discussion_button,
            panel_death_line,
            history_log,
            game_over_panel,
            game_over_status,
            discussion_title,
            live_day_card,
            # Only begin_discussion actually changes these (revealing the
            # night's kill hidden by _next_day_setup/start_game until now)
            # -- every other event sharing this output list (_stream_bridge,
            # send_discussion_turn, pass_discussion_turn) leaves them
            # untouched.
            alive_panel,
            deaths_panel,
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

        # Voting is no longer gated behind a "Begin Voting" click -- as soon
        # as discussion_status's text changes (set by _stream_bridge when
        # FlowStatus.DISCUSSION_COMPLETE arrives), the ballot starts opening
        # on its own.
        # discussion_status is both the trigger for this event and one of
        # its outputs (see start_voting's VoteOutcome branch, which swaps
        # its ballot question for "The Village Votes" label once the
        # human-is-dead path's outcome is known) -- safe against re-firing
        # itself since start_voting's bridge.resolve_input() guard makes any
        # re-entry from that second .change() a no-op.
        discussion_status.change(
            fn=start_voting,
            inputs=[session_bridge, game_state],
            outputs=[
                session_bridge,
                vote_button_row,
                *candidate_buttons,
                vote_status,
                discussion_status,
                alive_panel,
                lynched_panel,
                deaths_panel,
                begin_discussion_button,
                discussion_title,
                history_log,
                discussion_transcript,
                panel_death_line,
                game_over_panel,
                game_over_status,
                live_day_card,
            ],
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
            game_over_panel,
            game_over_status,
            live_day_card,
        ]

        # SessionBridge.resolve_input()'s no-pending-future guard (see
        # begin_discussion_button.click above) is what protects a
        # double-click here now -- cast_player_vote/cast_player_abstain no
        # longer call vote-casting logic directly, they resolve the bridge
        # and let VillageFlow.run_voting drive the AI kickoffs. Once voting
        # completes, cast_player_vote advances straight into the next day's
        # Begin-gated panel itself (see _next_day_setup) -- there's no
        # separate "Continue" step to wire up here anymore.
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
        js=_autoscroll_js() + _autofocus_js() + _game_over_scroll_js(),
    )


if __name__ == "__main__":
    main()
