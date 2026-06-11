"""EventDigest — a compact, factual log of what a behavior did on autopilot.

Behaviors run for minutes of game time without an LLM turn. When an interrupt
finally hands control back, the LLM needs to know what happened in the meantime
*without* replaying hundreds of raw actions. The digest aggregates events into
counted lines ("killed bandit (2)", "ate plump helmet", "+1 MACE") and renders
a short block for the post-interrupt prompt. On behavior end it also produces a
single one-line summary used for the history entry and the episodic memory note.
"""

from __future__ import annotations

from collections import Counter


class EventDigest:
    """Counted, ordered event aggregator. Cheap and allocation-light."""

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()
        self._order: list[str] = []  # first-seen order, for stable rendering
        self.actions = 0  # total behavior steps that did something
        self.start_tick: int | None = None
        self.end_tick: int | None = None

    def add(self, event: str, n: int = 1) -> None:
        """Record `n` occurrences of a factual event string."""
        event = event.strip()
        if not event:
            return
        if event not in self._counts:
            self._order.append(event)
        self._counts[event] += n

    def mark_action(self) -> None:
        self.actions += 1

    def note_tick(self, tick: int) -> None:
        if self.start_tick is None:
            self.start_tick = tick
        self.end_tick = tick

    @property
    def ticks(self) -> int:
        if self.start_tick is None or self.end_tick is None:
            return 0
        return max(0, self.end_tick - self.start_tick)

    @property
    def is_empty(self) -> bool:
        return not self._order

    def _lines(self, max_lines: int) -> list[str]:
        lines = []
        for event in self._order[:max_lines]:
            c = self._counts[event]
            lines.append(f"{event} ({c})" if c > 1 else event)
        if len(self._order) > max_lines:
            lines.append(f"... and {len(self._order) - max_lines} more event types")
        return lines

    def render(self, *, behavior_name: str = "autopilot", max_lines: int = 12) -> str:
        """Multi-line block injected into the post-interrupt turn prompt."""
        header = (
            f"-- While on autopilot ({behavior_name}, "
            f"{self.actions} actions, {self.ticks} ticks) --"
        )
        if self.is_empty:
            return header + "\n  (no notable events)"
        return header + "\n" + "\n".join(f"  {ln}" for ln in self._lines(max_lines))

    def one_line(self, *, behavior_name: str = "autopilot") -> str:
        """Single-line summary for history entries and memory notes."""
        if self.is_empty:
            return f"{behavior_name}: {self.actions} actions, no notable events"
        summary = "; ".join(self._lines(max_lines=6))
        return f"{behavior_name} ({self.actions} actions): {summary}"
