import logging
import time

import gradio as gr

from the_village.discussion import AdvanceStatus, DiscussionRunner, advance, start_discussion
from the_village.main import VillageFlow
from the_village.state import WEEKDAYS, GameState

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
PINNED_BAR_CLASS = "pinned-bar"
CHIP_LIST_CLASS = "chip-list"
VILLAGER_CHIP_CLASS = "villager-chip"
TYPING_INDICATOR_CLASS = "typing-indicator"

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
    return f"""
    :root {{ {light_vars}; }}
    @media (prefers-color-scheme: dark) {{
        :root {{ {dark_vars}; }}
    }}
    .dark {{ {dark_vars}; }}
    .{DISCUSSION_TRANSCRIPT_CLASS} .speaker-name {{ font-weight: 600; }}
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


def _speaker_color_index(name: str, state: GameState) -> int:
    roster_names = [villager.name for villager in state.villagers]
    if name not in roster_names:
        return 0
    return roster_names.index(name) % len(SPEAKER_COLORS)


def format_event_log(state: GameState) -> str:
    if not state.deaths:
        return "Nothing has happened yet."
    lines = [
        f"{WEEKDAYS[(death.day_number - 1) % 7]} morning: {death.name} was found dead."
        for death in state.deaths
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


def _living_ai_names(state: GameState) -> list[str]:
    return [
        villager.name
        for villager in state.villagers
        if villager.is_alive and villager.player_type in ("villager", "werewolf")
    ]


def _drive_discussion(runner, events, dropdown_choices=None):
    pending_choices = dropdown_choices
    try:
        for event in events:
            if isinstance(event, AdvanceStatus):
                transcript = format_discussion_transcript(runner.state)
                complete = event == AdvanceStatus.COMPLETE
                # Reset the textbox and "address to" dropdown once per driven
                # sequence (on the status yield), not on every message yield.
                dropdown_reset = (
                    gr.update(choices=pending_choices, value=None)
                    if pending_choices is not None
                    else gr.update(value=None)
                )
                yield (
                    runner,
                    transcript,
                    gr.update(value=""),
                    dropdown_reset,
                    gr.update(visible=not complete),
                    gr.update(
                        visible=complete,
                        value="The discussion has ended." if complete else "",
                    ),
                    gr.update(),
                )
                pending_choices = None
            else:
                # Pace AI turns to reading speed with a "typing" placeholder;
                # the player's own message (already visible to them as they
                # typed it) shows immediately with no delay.
                if event.speaker != runner.state.player_name:
                    pending_transcript = format_discussion_transcript(
                        runner.state, pending_speaker=event.speaker
                    )
                    yield (
                        runner,
                        pending_transcript,
                        gr.update(),
                        gr.update(),
                        gr.update(visible=False),
                        gr.update(),
                        gr.update(),
                    )
                    time.sleep(SPEAKER_THINKING_DELAY_SECONDS)
                transcript = format_discussion_transcript(runner.state)
                yield (
                    runner,
                    transcript,
                    gr.update(),
                    gr.update(),
                    gr.update(visible=False),
                    gr.update(),
                    gr.update(),
                )
    except gr.Error:
        raise
    except Exception as exc:
        logger.exception("Discussion turn failed")
        raise gr.Error("Something went wrong, please try again.") from exc


def begin_discussion(state: GameState):
    runner = start_discussion(state)
    # Hide the button the instant it's clicked, before driving any AI turns
    # -- generating the first villager's turn can block on an LLM call, and
    # the button shouldn't linger visible while that happens.
    yield (
        runner,
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(visible=False),
    )
    yield from _drive_discussion(
        runner,
        advance(runner),
        dropdown_choices=_living_ai_names(state),
    )


def send_discussion_turn(runner: DiscussionRunner, message: str, addressed_to: str):
    if not message.strip():
        raise gr.Error('Type something, or click "I have nothing to say."')
    events = advance(
        runner,
        player_input=message.strip(),
        player_addressed_to=addressed_to or None,
    )
    yield from _drive_discussion(runner, events)


def pass_discussion_turn(runner: DiscussionRunner):
    # Hide the input row the instant the player passes, before driving any
    # AI turns -- generating the next villager's turn can block on an LLM
    # call, and the row shouldn't linger open while that happens.
    yield (
        runner,
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(visible=False),
        gr.update(),
        gr.update(),
    )
    yield from _drive_discussion(runner, advance(runner, player_pass=True))


def start_game(player_name: str):
    if not player_name or not player_name.strip():
        raise gr.Error("Please enter your name.")

    try:
        flow = VillageFlow()
        flow.kickoff(inputs={"player_name": player_name.strip()})
    except gr.Error:
        raise
    except Exception as exc:
        logger.exception("Flow kickoff failed")
        raise gr.Error("Something went wrong, please try again.") from exc

    state = flow.state
    return (
        gr.update(visible=False),
        gr.update(visible=True),
        format_event_log(state),
        format_deaths_panel(state),
        format_alive_panel(state),
        state,
    )


def build_app() -> gr.Blocks:
    with gr.Blocks(title="The Village") as demo:
        game_state = gr.State()
        discussion_runner_state = gr.State()

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
                gr.Markdown("### Events")
                event_log = gr.Markdown()
                begin_discussion_button = gr.Button("Begin Discussion")
                discussion_transcript = gr.Markdown(
                    elem_classes=[DISCUSSION_TRANSCRIPT_CLASS]
                )
                with gr.Row(visible=False) as discussion_input_row:
                    discussion_textbox = gr.Textbox(label="Say something", scale=3)
                    discussion_addressed_to = gr.Dropdown(
                        label="Address to (optional)", choices=[], scale=1
                    )
                    with gr.Column(scale=1):
                        send_button = gr.Button("Send")
                        pass_button = gr.Button("I have nothing to say")
                discussion_status = gr.Markdown(visible=False)

        start_button.click(
            fn=start_game,
            inputs=[name_input],
            outputs=[
                start_screen,
                result_screen,
                event_log,
                deaths_panel,
                alive_panel,
                game_state,
            ],
            concurrency_limit=None,
        )

        discussion_outputs = [
            discussion_runner_state,
            discussion_transcript,
            discussion_textbox,
            discussion_addressed_to,
            discussion_input_row,
            discussion_status,
            begin_discussion_button,
        ]

        # These three handlers all mutate the same DiscussionRunner/GameState.discussion.
        # Sharing one concurrency slot keeps overlapping clicks (e.g. a double-click
        # while a turn is still streaming) queued instead of racing on that shared state.
        begin_discussion_button.click(
            fn=begin_discussion,
            inputs=[game_state],
            outputs=discussion_outputs,
            concurrency_limit=1,
            concurrency_id="discussion_turn",
        )

        send_button.click(
            fn=send_discussion_turn,
            inputs=[discussion_runner_state, discussion_textbox, discussion_addressed_to],
            outputs=discussion_outputs,
            concurrency_limit=1,
            concurrency_id="discussion_turn",
        )

        discussion_textbox.submit(
            fn=send_discussion_turn,
            inputs=[discussion_runner_state, discussion_textbox, discussion_addressed_to],
            outputs=discussion_outputs,
            concurrency_limit=1,
            concurrency_id="discussion_turn",
        )

        pass_button.click(
            fn=pass_discussion_turn,
            inputs=[discussion_runner_state],
            outputs=discussion_outputs,
            concurrency_limit=1,
            concurrency_id="discussion_turn",
        )

    return demo


def main():
    build_app().launch(css=_speaker_color_css() + _layout_css(), js=_autoscroll_js())


if __name__ == "__main__":
    main()
