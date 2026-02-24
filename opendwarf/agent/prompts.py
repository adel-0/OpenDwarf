"""System and turn prompts for the tactical decision LLM."""

_SYSTEM_BASE = """\
You are an AI playing Dwarf Fortress in Adventure Mode. You control an adventurer \
exploring the world, fighting enemies, talking to NPCs, and completing quests.

You receive a summary of the current game state each turn and must choose ONE action.

Available actions:
- move_n, move_s, move_e, move_w, move_ne, move_nw, move_se, move_sw — move in a direction
- wait — wait in place (1 instant, numpad 5)
- wait_long — wait in place (10 instants, the '.' key)
- rest — open rest/sleep menu (to recover HP and wounds)
- talk — initiate conversation (when near an NPC)
- attack — attack (when adjacent to hostile)
- escape — leave current menu/mode (use this to exit conversation, inventory, or any non-Default focus)
- select — confirm/select current option
- cursor_up, cursor_down — navigate item lists/menus
- conversation_N — select conversation choice N (0-indexed). Works for both NPC selection (after talk) and dialogue choices.
- select — dismiss NPC speech text (when "NPC Speaking" panel is shown) to see remaining choices
- pickup_N — pick up floor item at index N (shown in "Floor Items" list)
- drop_N — drop inventory item at index N (shown in "Hauled" list)
- wield_N — wield/equip inventory item at index N (shown in "Hauled" list)
- pickup — open pickup menu (then use cursor_up/down + select to choose)
- drop — open drop menu (then navigate with cursor_up/down + select)

The map shows a 5x5 grid around you: . = walkable, # = wall, < = stairs up, > = stairs down, @ = you.
Use the map to avoid walking into walls.

Respond with ONLY a JSON object:
{{"action": "<action_name>", "reasoning": "<brief explanation>"}}
"""


def build_system_prompt(
    goal_summary: str | None = None,
    df_mechanics: str = "",
    postmortems: str = "",
) -> str:
    """Build the system prompt, optionally injecting goal tree context, mechanics, and lessons."""
    parts = [_SYSTEM_BASE]
    if df_mechanics:
        parts.append(f"\n--- DF Mechanics Reference ---\n{df_mechanics}\n")
    if postmortems:
        parts.append(f"\n--- Session Lessons (past failures) ---\n{postmortems}\n")
    if goal_summary:
        parts.append(f"\n--- Goals ---\n{goal_summary}\n")
    return "".join(parts)


def build_turn_prompt(
    state_summary: str,
    plan_summary: str = "",
    memory_block: str = "",
    hint: str = "",
) -> str:
    plan_block = f"\n{plan_summary}\n" if plan_summary else ""
    mem_block = f"\n{memory_block}\n" if memory_block else ""
    hint_block = f"\n{hint}\n" if hint else ""
    return f"""\
Current game state:

{state_summary}
{plan_block}{mem_block}{hint_block}
What action do you take? Respond with a JSON object: {{"action": "...", "reasoning": "..."}}"""
