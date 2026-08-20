import asyncio
import logging

import gradio as gr

from the_village.bridge import FlowFailed, FlowStatus, PlayerInput, SessionBridge, run_flow
from the_village.main import VillageFlow
from the_village.state import WEEKDAYS, DiscussionMessage, GameState
from the_village.voting import VoteOutcome, cast_votes

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
        f'.{VOTE_CANDIDATE_BUTTON_CLASS}.speaker-btn-{i} {{ '
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
        background: #2e7d32;
        border-color: #2e7d32;
        color: #fff;
    }}
    .{BEGIN_DISCUSSION_BUTTON_CLASS}:hover {{
        background: #276729;
        border-color: #276729;
    }}
    """


def _autoscroll_js() -> str:
    # The transcript streams in as separate yields during a discussion turn,
    # so we can't hook a single event's completion to know when to scroll.
    # A MutationObserver reacts to every content change instead, regardless
    # of how many times the Markdown gets updated.
    return f"""
    (() => {{
        const attach = () => {{
            const transcript = document.querySelector(".{DISCUSSION_TRANSCRIPT_CLASS}");
            const scrollContainer = document.querySelector(".gradio-container");
            if (!transcript || !scrollContainer) {{
                setTimeout(attach, 200);
                return;
            }}
            const scrollToBottom = () => {{
                scrollContainer.scrollTop = scrollContainer.scrollHeight;
            }};
            new MutationObserver(scrollToBottom).observe(transcript, {{
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
    roster_names = [villager.name for villager in state.villagers]
    if name not in roster_names:
        return 0
    return roster_names.index(name) % len(SPEAKER_COLORS)


DEATH_MESSAGE_TEMPLATES = [
    "{name} was found dead, torn apart by a werewolf attack.",
    "{name} didn't survive the night — a werewolf got to them first.",
    "The pack struck again: {name} was found mauled to death.",
    "{name} was found dead, ravaged by wolf jaws in the dark of night.",
    "A werewolf attack claimed {name} overnight; their body was found at dawn.",
    "{name} never made it to morning, savaged by a werewolf under cover of darkness.",
]


def format_event_log(state: GameState) -> str:
    if not state.deaths:
        return "<strong>Nothing has happened yet.</strong>"
    lines = [
        "<strong>"
        f"{WEEKDAYS[(death.day_number - 1) % 7]} morning: "
        f'<span class="{DEATH_LINE_CLASS}">'
        + DEATH_MESSAGE_TEMPLATES[index % len(DEATH_MESSAGE_TEMPLATES)].format(
            name=death.name
        )
        + "</span></strong>"
        for index, death in enumerate(state.deaths)
    ]
    return "\n\n".join(lines)


def format_deaths_panel(state: GameState) -> str:
    if not state.deaths:
        return f'<div class="{CHIP_LIST_CLASS}">No one has been killed yet.</div>'
    chips = "".join(
        f'<span class="{VILLAGER_CHIP_CLASS} dead" '
        f'style="color: var(--speaker-{_speaker_color_index(death.name, state)})">'
        f"{death.name}</span>"
        for death in state.deaths
    )
    return f'<div class="{CHIP_LIST_CLASS}">{chips}</div>'


def format_lynched_panel(state: GameState) -> str:
    if not state.lynchings:
        return f'<div class="{CHIP_LIST_CLASS}">No one has been lynched yet.</div>'
    chips = "".join(
        f'<span class="{VILLAGER_CHIP_CLASS} dead" '
        f'style="color: var(--speaker-{_speaker_color_index(lynching.name, state)})">'
        f"{lynching.name}</span>"
        for lynching in state.lynchings
    )
    return f'<div class="{CHIP_LIST_CLASS}">{chips}</div>'


def _vote_candidate_names(state: GameState) -> list[str]:
    return [
        villager.name
        for villager in state.villagers
        if villager.is_alive and villager.name != state.player_name
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
                    elem_classes=[VOTE_CANDIDATE_BUTTON_CLASS, f"speaker-btn-{color_index}"],
                )
            )
        else:
            updates.append(gr.Button(visible=False))
    return updates


def format_alive_panel(state: GameState) -> str:
    alive = [villager for villager in state.villagers if villager.is_alive]
    if not alive:
        return f'<div class="{CHIP_LIST_CLASS}">No one is left.</div>'
    chips = "".join(
        f'<span class="{VILLAGER_CHIP_CLASS}" '
        f'style="color: var(--speaker-{_speaker_color_index(villager.name, state)})">'
        f'{villager.name}{" (me)" if villager.name == state.player_name else ""}'
        f"</span>"
        for villager in alive
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


def format_discussion_transcript(
    state: GameState, pending_speaker: str | None = None
) -> str:
    # `pending_speaker` hides the last message (already appended to
    # state.discussion by the time this is called) and shows a "typing"
    # placeholder for that speaker instead, so the reveal can be paced.
    messages = state.discussion[:-1] if pending_speaker is not None else state.discussion
    lines = [
        f"{_speaker_name_span(m.speaker, state)} {m.message}" for m in messages
    ]
    if pending_speaker is not None:
        lines.append(
            f'{_speaker_name_span(pending_speaker, state)} '
            f'<span class="{TYPING_INDICATOR_CLASS}"><span></span><span></span><span></span></span>'
        )
    if not lines:
        return ""
    return "\n\n".join(lines)


async def _stream_bridge(bridge: SessionBridge, state: GameState):
    """Drains bridge.outbox, yielding a UI-update tuple per item, until a
    status tells the caller to stop and hand control back to the player."""
    try:
        while True:
            item = await bridge.outbox.get()
            if isinstance(item, FlowFailed):
                raise gr.Error("Something went wrong, please try again.")
            if isinstance(item, DiscussionMessage):
                # Accumulate into this session's own GameState as messages
                # arrive so the transcript renders live -- the background
                # DiscussionFlow appends to a *copy* of this state (hydrated
                # from a model_dump()), so without this the transcript panel
                # stays blank until the whole discussion finishes.
                state.discussion.append(item)
                # Pace AI turns to reading speed with a "typing" placeholder;
                # the player's own message (already visible to them as they
                # typed it) shows immediately with no delay.
                if item.speaker != state.player_name:
                    pending_transcript = format_discussion_transcript(
                        state, pending_speaker=item.speaker
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
                transcript = format_discussion_transcript(state)
                yield (
                    bridge,
                    transcript,
                    gr.update(value=""),
                    gr.update(visible=False),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                )
            elif item == FlowStatus.WAITING_FOR_TURN or item == FlowStatus.WAITING_FOR_ANSWER:
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
    weekday = WEEKDAYS[(state.day_number - 1) % 7]
    yield (
        bridge,
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(visible=False),
        gr.update(value=f"### {weekday}'s Discussion", visible=True),
    )
    async for update in _stream_bridge(bridge, state):
        yield update


async def send_discussion_turn(bridge: SessionBridge, state: GameState, message: str):
    if not message.strip():
        yield (gr.skip(),) * 7
        return
    if not bridge.resolve_input(PlayerInput(message=message.strip())):
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
    if not bridge.resolve_input(PlayerInput(message=None)):
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


def begin_voting(state: GameState):
    weekday = WEEKDAYS[(state.day_number - 1) % 7]
    return (
        gr.update(visible=False),  # begin_voting_button
        gr.update(value=f"### {weekday}'s Voting", visible=True),  # voting_title
        gr.update(visible=True),  # vote_button_row
        *_vote_button_updates(state),
        gr.update(visible=False),  # vote_status
        gr.update(),  # discussion_status stays visible during voting
    )


def format_vote_result(state: GameState, outcome: VoteOutcome) -> str:
    lines = [
        f"{_colored_name(record.voter, state)} voted for "
        f"{_colored_name(record.target, state)}."
        if record.target is not None
        else f"{_colored_name(record.voter, state)} abstained."
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
        lines.append(f"**{_colored_name(outcome.lynched, state)} was lynched by the village.**")
    elif outcome.tally:
        lines.append("**The vote was tied — no one was lynched.**")
    else:
        lines.append("**No one voted to lynch anyone — no one was lynched.**")
    return "\n\n".join(lines)


def cast_player_vote(state: GameState, bridge: SessionBridge, target: str | None):
    # Hide the ballot the instant the player votes, before the blocking AI
    # kickoff calls run -- the row shouldn't linger visible while they resolve.
    yield (
        gr.update(visible=False),
        gr.update(value="Tallying the votes…", visible=True),
        gr.update(),
        gr.update(),
    )
    try:
        outcome = cast_votes(state, bridge.agents, player_vote=target)
    except gr.Error:
        raise
    except Exception as exc:
        logger.exception("Vote casting failed")
        # Restore the ballot so the player can actually retry -- leaving it
        # hidden after the error would strand them with no way to vote again.
        yield (
            gr.update(visible=True),
            gr.update(
                value="Something went wrong — try voting again.", visible=True
            ),
            gr.update(),
            gr.update(),
        )
        raise gr.Error("Something went wrong, please try again.") from exc
    yield (
        gr.update(visible=False),
        gr.update(value=format_vote_result(state, outcome), visible=True),
        format_alive_panel(state),
        format_lynched_panel(state),
    )


def cast_player_abstain(state: GameState, bridge: SessionBridge):
    yield from cast_player_vote(state, bridge, None)


async def start_game(player_name: str):
    if not player_name or not player_name.strip():
        raise gr.Error("Please enter your name.")

    bridge = SessionBridge()
    village_flow = VillageFlow(bridge=bridge)
    bridge.task = asyncio.create_task(
        run_flow(
            village_flow.kickoff_async(inputs={"player_name": player_name.strip()}), bridge
        )
    )

    item = await bridge.outbox.get()
    if isinstance(item, FlowFailed):
        raise gr.Error("Something went wrong, please try again.")

    state = village_flow.state
    yield (
        gr.update(visible=False),
        gr.update(visible=True),
        format_event_log(state),
        format_deaths_panel(state),
        format_alive_panel(state),
        format_lynched_panel(state),
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

        with gr.Column(visible=False) as result_screen:
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
                event_log = gr.Markdown()
                begin_discussion_button = gr.Button(
                    "Begin Discussion", elem_classes=[BEGIN_DISCUSSION_BUTTON_CLASS]
                )
                discussion_title = gr.Markdown(
                    visible=False, elem_classes=[DISCUSSION_TITLE_CLASS]
                )
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
                voting_title = gr.Markdown(
                    visible=False, elem_classes=[DISCUSSION_TITLE_CLASS]
                )
                with gr.Row(visible=False) as vote_button_row:
                    candidate_buttons = [
                        gr.Button(visible=False) for _ in range(MAX_VOTE_CANDIDATES)
                    ]
                    abstain_button = gr.Button("Abstain")
                vote_status = gr.Markdown(visible=False)

        start_button.click(
            fn=start_game,
            inputs=[name_input],
            outputs=[
                start_screen,
                result_screen,
                event_log,
                deaths_panel,
                alive_panel,
                lynched_panel,
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
            discussion_title,
        ]

        # SessionBridge.resolve_input() is the per-session no-pending-future
        # guard that replaces the old shared "discussion_turn" concurrency_id
        # -- a double-click (or overlapping submit) becomes a no-op instead
        # of racing on shared state, without serializing unrelated sessions.
        begin_discussion_button.click(
            fn=begin_discussion,
            inputs=[session_bridge, game_state],
            outputs=discussion_outputs,
        )

        send_button.click(
            fn=send_discussion_turn,
            inputs=[session_bridge, game_state, discussion_textbox],
            outputs=discussion_outputs,
        )

        discussion_textbox.submit(
            fn=send_discussion_turn,
            inputs=[session_bridge, game_state, discussion_textbox],
            outputs=discussion_outputs,
        )

        pass_button.click(
            fn=pass_discussion_turn,
            inputs=[session_bridge, game_state],
            outputs=discussion_outputs,
        )

        begin_voting_button.click(
            fn=begin_voting,
            inputs=[game_state],
            outputs=[
                begin_voting_button,
                voting_title,
                vote_button_row,
                *candidate_buttons,
                vote_status,
                discussion_status,
            ],
        )

        discussion_status.change(
            fn=lambda status_text: gr.update(visible=bool(status_text)),
            inputs=[discussion_status],
            outputs=[begin_voting_button],
        )

        vote_outputs = [vote_button_row, vote_status, alive_panel, lynched_panel]

        # Both handlers mutate the same shared GameState via cast_votes() and
        # block on AI kickoff calls -- sharing one concurrency slot with
        # cast_player_abstain prevents a double-click (or clicking a
        # candidate and Abstain in quick succession) from racing on that
        # shared state, mirroring the "discussion_turn" guard above.
        for button in candidate_buttons:
            button.click(
                fn=cast_player_vote,
                inputs=[game_state, session_bridge, button],
                outputs=vote_outputs,
                concurrency_limit=1,
                concurrency_id="vote_cast",
            )

        abstain_button.click(
            fn=cast_player_abstain,
            inputs=[game_state, session_bridge],
            outputs=vote_outputs,
            concurrency_limit=1,
            concurrency_id="vote_cast",
        )

    return demo


def main():
    build_app().launch(
        css=_speaker_color_css() + _layout_css(),
        js=_autoscroll_js() + _autofocus_js(),
    )


if __name__ == "__main__":
    main()
