import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from the_village.ui import (
    _autofocus_js,
    _autofocus_name_js,
    _autoscroll_js,
    _chronicle_css,
    _font_import_css,
    _game_over_scroll_js,
    _layout_css,
    _speaker_color_css,
    _viewport_head,
    build_app,
)


def main():
    build_app().launch(
        css=_font_import_css()
        + _speaker_color_css()
        + _layout_css()
        + _chronicle_css(),
        js=_autoscroll_js()
        + _autofocus_js()
        + _autofocus_name_js()
        + _game_over_scroll_js(),
        head=_viewport_head(),
    )


if __name__ == "__main__":
    main()
