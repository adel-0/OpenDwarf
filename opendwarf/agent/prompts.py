"""System and turn prompts for the tactical decision LLM."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opendwarf.state.game_state import GameState

_SYSTEM_BASE = """\
You are an AI playing Dwarf Fortress in Adventure Mode. You control an adventurer \
exploring the world, fighting enemies, talking to NPCs, and completing quests.

You receive a summary of the current game state each turn and must choose ONE action.
Available actions vary by context and are shown each turn.

The map shows a 5x5 grid around you: . = walkable, # = wall, < = stairs up, > = stairs down, @ = you.
Use the map to avoid walking into walls.

Respond with ONLY a JSON object:
{{"action": "<action_name>", "reasoning": "<brief explanation>"}}
"""


def build_action_block(state: GameState, banned: set[str] | None = None) -> str:
    """Generate context-appropriate action list based on game state."""
    banned = banned or set()
    lines: list[str] = []

    if state.conversation_phase == "select_npc":
        lines.append("--- Available Actions (Conversation: Select NPC) ---")
        # Check if all choices are system options (no real NPCs)
        all_system = all(
            "adventure_option_" in c.text.lower() or "shout" in c.text.lower()
            for c in state.conversation_choices
        ) if state.conversation_choices else True
        if all_system and state.conversation_choices:
            lines.append("No NPCs nearby to talk to. Use escape to close this menu.")
        else:
            lines.append("You opened the conversation menu. Select an NPC by index, or escape to cancel.")
        actions = []
        for c in state.conversation_choices:
            a = f"conversation_{c.index}"
            if a not in banned:
                actions.append(a)
        if "escape" not in banned:
            actions.append("escape")
        for a in actions:
            lines.append(f"  {a}")

    elif state.conversation_phase == "dialogue":
        lines.append("--- Available Actions (Dialogue) ---")
        lines.append("You are in dialogue. Select a response by index, or escape to end conversation.")
        actions = []
        for c in state.conversation_choices:
            a = f"conversation_{c.index}"
            if a not in banned:
                actions.append(a)
        if "escape" not in banned:
            actions.append("escape")
        for a in actions:
            lines.append(f"  {a}")

    elif state.in_combat or state.hostile_units:
        lines.append("--- Available Actions (COMBAT) ---")
        lines.append("You are in combat!")
        combat_actions = [
            ("attack", "attack adjacent hostile"),
            ("move_n", None), ("move_s", None), ("move_e", None), ("move_w", None),
            ("move_ne", None), ("move_nw", None), ("move_se", None), ("move_sw", None),
            ("wait", "wait in place (1 instant)"),
            ("escape", "leave current menu/mode"),
        ]
        for item in combat_actions:
            a = item[0]
            if a not in banned:
                desc = f" — {item[1]}" if item[1] else ""
                lines.append(f"  {a}{desc}")

    elif state.fast_travel_active:
        lines.append("--- Available Actions (FAST TRAVEL) ---")
        lines.append("You are in fast travel mode. Move across the world map quickly.")
        lines.append("Movement (one step = many local tiles):")
        go_dirs = ["move_n", "move_s", "move_e", "move_w", "move_ne", "move_nw", "move_se", "move_sw"]
        go_line = ", ".join(d for d in go_dirs if d not in banned)
        if go_line:
            lines.append(f"  {go_line}")
        lines.append("Other:")
        if "stop_travel" not in banned:
            lines.append("  stop_travel — exit fast travel and return to local mode")
        if "wait" not in banned:
            lines.append("  wait — wait in place")

    else:
        lines.append("--- Available Actions ---")
        lines.append("Movement (autopilot: moves multiple tiles, avoiding walls):")
        go_dirs = ["go_north", "go_south", "go_east", "go_west", "go_ne", "go_nw", "go_se", "go_sw"]
        go_line = ", ".join(d for d in go_dirs if d not in banned)
        if go_line:
            lines.append(f"  {go_line}")
        # Show approach_unit for non-adjacent friendly units (dist > 1)
        friendly_far = [u for u in state.nearby_units if not u.is_hostile and u.distance > 1]
        if friendly_far:
            for u in friendly_far[:5]:
                a = f"approach_unit:{u.id}"
                if a not in banned:
                    lines.append(f"  {a} — move toward {u.name} ({u.race}, dist={u.distance})")
        lines.append("Other actions:")
        other_actions = [
            ("wait", "wait in place (1 instant)"),
            ("wait_long", "wait in place (10 instants)"),
            ("talk", "initiate conversation (when near an NPC)"),
            ("travel", "enter fast travel mode (for long-distance travel between sites)"),
            ("attack", "attack adjacent hostile"),
            ("rest", "open rest/sleep menu"),
            ("escape", "leave current menu/mode"),
        ]
        for item in other_actions:
            a = item[0]
            if a not in banned:
                lines.append(f"  {a} — {item[1]}")

        # Item actions — only show when relevant
        if state.floor_items:
            for i, fi in enumerate(state.floor_items):
                a = f"pickup_{i}"
                if a not in banned:
                    lines.append(f"  {a} — pick up {fi.name}")
        hauled = [item for item in state.inventory if item.mode == "Hauled"]
        if hauled:
            for i, hi in enumerate(hauled):
                da = f"drop_{i}"
                wa = f"wield_{i}"
                if da not in banned:
                    lines.append(f"  {da} — drop {hi.name}")
                if wa not in banned:
                    lines.append(f"  {wa} — wield {hi.name}")

    return "\n".join(lines)


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
    action_block: str = "",
    plan_summary: str = "",
    memory_block: str = "",
    hint: str = "",
    announcement_block: str = "",
) -> str:
    plan_block = f"\n{plan_summary}\n" if plan_summary else ""
    mem_block = f"\n{memory_block}\n" if memory_block else ""
    hint_block = f"\n{hint}\n" if hint else ""
    action_section = f"\n{action_block}\n" if action_block else ""
    ann_block = f"\n{announcement_block}\n" if announcement_block else ""
    return f"""\
Current game state:

{state_summary}
{ann_block}{action_section}{plan_block}{mem_block}{hint_block}
What action do you take? Respond with a JSON object: {{"action": "...", "reasoning": "..."}}"""
