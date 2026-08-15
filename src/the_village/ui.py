import logging

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
    ("#1baf7a", "#199e70"),  # aqua
    ("#eda100", "#c98500"),  # yellow
    ("#e87ba4", "#d55181"),  # magenta
    ("#008300", "#008300"),  # green
    ("#4a3aa7", "#9085e9"),  # violet
    ("#e34948", "#e66767"),  # red
]

DISCUSSION_TRANSCRIPT_CLASS = "discussion-transcript"


def _speaker_color_css() -> str:
    light_vars = "; ".join(
        f"--speaker-{i}: {light}" for i, (light, _dark) in enumerate(SPEAKER_COLORS)
    )
    dark_vars = "; ".join(
        f"--speaker-{i}: {dark}" for i, (_light, dark) in enumerate(SPEAKER_COLORS)
    )
    return f"""
    .{DISCUSSION_TRANSCRIPT_CLASS} {{ {light_vars}; }}
    @media (prefers-color-scheme: dark) {{
        .{DISCUSSION_TRANSCRIPT_CLASS} {{ {dark_vars}; }}
    }}
    .dark .{DISCUSSION_TRANSCRIPT_CLASS} {{ {dark_vars}; }}
    .{DISCUSSION_TRANSCRIPT_CLASS} .speaker-name {{ font-weight: 600; }}
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
        return "No one has been killed yet."
    lines = [
        f"- {WEEKDAYS[(death.day_number - 1) % 7]}: {death.name}"
        for death in state.deaths
    ]
    return "\n".join(lines)


def format_alive_panel(state: GameState) -> str:
    alive = [villager for villager in state.villagers if villager.is_alive]
    if not alive:
        return "No one is left."
    lines = [
        f"- {villager.name} (me)" if villager.name == state.player_name else f"- {villager.name}"
        for villager in alive
    ]
    return "\n".join(lines)


def format_discussion_transcript(state: GameState) -> str:
    if not state.discussion:
        return "The discussion hasn't started yet."
    lines = [
        f'<span class="speaker-name" '
        f'style="color: var(--speaker-{_speaker_color_index(m.speaker, state)})">'
        f"{m.speaker}:</span> {m.message}"
        for m in state.discussion
    ]
    return "\n\n".join(lines)


def _living_ai_names(state: GameState) -> list[str]:
    return [
        villager.name
        for villager in state.villagers
        if villager.is_alive and villager.player_type in ("villager", "werewolf")
    ]


def _drive_discussion(runner, events, dropdown_choices=None, begin_button_update=None):
    pending_choices = dropdown_choices
    pending_begin_button_update = (
        begin_button_update if begin_button_update is not None else gr.update()
    )
    try:
        for event in events:
            transcript = format_discussion_transcript(runner.state)
            if isinstance(event, AdvanceStatus):
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
                    pending_begin_button_update,
                )
                pending_choices = None
                pending_begin_button_update = gr.update()
            else:
                yield (
                    runner,
                    transcript,
                    gr.update(),
                    gr.update(),
                    gr.update(visible=False),
                    gr.update(),
                    pending_begin_button_update,
                )
    except gr.Error:
        raise
    except Exception as exc:
        logger.exception("Discussion turn failed")
        raise gr.Error("Something went wrong, please try again.") from exc


def begin_discussion(state: GameState):
    runner = start_discussion(state)
    yield from _drive_discussion(
        runner,
        advance(runner),
        dropdown_choices=_living_ai_names(state),
        begin_button_update=gr.update(visible=False),
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

        with gr.Row(visible=False) as result_screen:
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
                    send_button = gr.Button("Send")
                    pass_button = gr.Button("I have nothing to say")
                discussion_status = gr.Markdown(visible=False)
            with gr.Column():
                gr.Markdown("### Alive Villagers")
                alive_panel = gr.Markdown()
                gr.Markdown("### Killed by Werewolves")
                deaths_panel = gr.Markdown()

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

        pass_button.click(
            fn=pass_discussion_turn,
            inputs=[discussion_runner_state],
            outputs=discussion_outputs,
            concurrency_limit=1,
            concurrency_id="discussion_turn",
        )

    return demo


def main():
    build_app().launch(css=_speaker_color_css())


if __name__ == "__main__":
    main()
