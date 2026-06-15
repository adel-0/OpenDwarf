"""Unit tests for the L3 `press:<KEY>` escape-hatch dispatch (registry.py).

Regression coverage for a bug surfaced by the live escape-hatch review
(ROADMAP / logs REVIEW.md): the LLM emits natural-language key names like
`press:ESCAPE`, which are NOT valid `df.interface_key` names (the real key is
LEAVESCREEN). They passed validation (alphanumeric, not blocklisted) and then
silently no-op'd at the Lua layer as repeated `console_error`s. The fix
normalizes the known aliases before dispatch while keeping the hatch permissive
for genuinely unmodeled keys.
"""

from __future__ import annotations

from opendwarf.actions.registry import ActionKind, default_registry

reg = default_registry()


def _press(action: str):
    # `press:` make() ignores state/ctx, so None/None is safe here.
    return reg.resolve(action, None, None)


def test_escape_aliases_to_leavescreen():
    d = _press("press:ESCAPE")
    assert d.kind is ActionKind.KEY
    assert d.key == "press:LEAVESCREEN"
    assert d.error is None
    # canonical preserves what the LLM actually said (for observability).
    assert d.canonical == "press:ESCAPE"


def test_alias_is_case_insensitive_and_trimmed():
    assert _press("press:esc").key == "press:LEAVESCREEN"
    assert _press("press: Enter ").key == "press:SELECT"


def test_select_family_aliases():
    for raw in ("ENTER", "RETURN", "CONFIRM", "OK"):
        assert _press(f"press:{raw}").key == "press:SELECT"


def test_leavescreen_family_aliases():
    for raw in ("ESCAPE", "ESC", "BACK", "CANCEL"):
        assert _press(f"press:{raw}").key == "press:LEAVESCREEN"


def test_valid_interface_key_passes_through_unchanged():
    d = _press("press:A_ATTACK")
    assert d.key == "press:A_ATTACK"
    assert d.error is None


def test_unknown_key_still_sent_permissively():
    # The hatch must not refuse keys we don't model — the LLM may legitimately
    # need one. Unknown-but-clean keys pass through with no error.
    d = _press("press:A_SOME_NEW_KEY")
    assert d.key == "press:A_SOME_NEW_KEY"
    assert d.error is None


def test_blocked_key_is_refused():
    d = _press("press:QUIT_GAME")
    assert d.key == "A_MOVE_SAME_SQUARE"
    assert d.error is not None
    assert "blocked" in d.error


def test_empty_key_is_refused():
    d = _press("press:")
    assert d.key == "A_MOVE_SAME_SQUARE"
    assert d.error is not None


def test_invalid_characters_refused():
    d = _press("press:foo bar")
    assert d.key == "A_MOVE_SAME_SQUARE"
    assert d.error is not None
