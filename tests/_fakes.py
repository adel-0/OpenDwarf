"""Shared test doubles for the DFHack seam.

Consolidates the per-file ``_FakeLua`` / ``_FakeExtractor`` / ``_FakePathfinder``
/ ``_FakeLLM`` classes that were copy-pasted across six test modules
(``test_combat_strike``, ``test_journey_behavior``, ``test_converse_skill``,
``test_grind_combat``, ``test_fast_travel``, ``test_site_registry``) into one set
of configurable fakes.

``SimulatedDF`` is the fake at the ``LuaExecutor`` boundary. Today it *records*
the actions/scripts issued through that seam and serves canned lookups — a
faithful superset of the former recorders. The intent (see the harness roadmap)
is to grow a stateful in-memory world behind this same method surface
(``execute_action`` / ``run_script`` / ``resolve_site``), so a behavior can run
end-to-end offline and, eventually, the same object can sit behind a real-vs-sim
backend flag. Keeping the method surface identical to ``LuaExecutor`` now is what
makes that later step a drop-in.

tests/ is not a package; pytest's default (prepend) import mode puts this
directory on sys.path, so test modules import these as ``from _fakes import ...``.
"""

from __future__ import annotations


class SimulatedDF:
    """Action-recording fake at the ``LuaExecutor`` seam.

    Superset of the former per-file ``_FakeLua`` variants:

    - ``execute_action(key)``         — records to ``actions``; returns ``[]``
      (matching ``LuaExecutor.execute_action``'s ``list[str]`` contract).
    - ``run_script(name, args=None)`` — records to ``scripts``; returns the
      canned output for ``name`` from ``script_output`` (``[]`` by default).
    - ``resolve_site(name)``          — records to ``queries``; returns the
      canned matches for ``name`` from ``matches_by_query`` (``[]`` by default).

    ``matches_by_query`` is the first positional arg to preserve the
    ``test_site_registry`` call style ``SimulatedDF({...})``.
    """

    def __init__(self, matches_by_query: dict | None = None,
                 script_output: dict | None = None):
        self.actions: list[str] = []
        self.scripts: list[str] = []
        self.queries: list[str] = []
        self._matches_by_query = matches_by_query or {}
        self._script_output = script_output or {}

    def execute_action(self, key):
        self.actions.append(key)
        return []

    def run_script(self, name, args=None):
        self.scripts.append(name)
        return list(self._script_output.get(name, []))

    def resolve_site(self, name):
        self.queries.append(name)
        return self._matches_by_query.get(name, [])


class FakeExtractor:
    """Stand-in for ``MapExtractor``'s coordinate helpers.

    ``offset`` is added to local coords to fake an absolute frame:
    ``(0, 0, 0)`` is the identity (used by conversation tests that never set a
    position); ``(1000, 1000, 0)`` fakes a region offset (movement/journey/grind
    tests). ``adventurer_abs`` returns ``None`` when the state has no position —
    which is the behavior the conversation tests relied on.
    """

    has_offset = True

    def __init__(self, offset: tuple[int, int, int] = (0, 0, 0)):
        self.offset = offset

    def adventurer_abs(self, state):
        p = state.adventurer_position
        if p is None:
            return None
        ox, oy, _ = self.offset
        return (ox + p.x, oy + p.y, p.z)

    def to_abs(self, x, y, z):
        ox, oy, _ = self.offset
        return (ox + x, oy + y, z)

    def ensure_fresh(self, state):
        pass


class FakePathfinder:
    """Stand-in for ``Pathfinder``. ``find_path`` returns the canned ``path``
    (or ``[]``); ``frontier_path`` always returns ``[]``."""

    def __init__(self, path=None):
        self.path = path

    def find_path(self, cur, goal, now_tick=0, partial=False):
        return list(self.path) if self.path else []

    def frontier_path(self, cur, direction, now_tick=0):
        return []


class FakeLLM:
    """Stand-in LLM client. ``decide`` returns a fixed ``payload`` and records
    the ``caller`` of each call (so tests can assert which model tier was used)."""

    def __init__(self, payload):
        self.payload = payload
        self.callers: list[str] = []

    def decide(self, bundle, *, caller="tactical"):
        self.callers.append(caller)
        return self.payload
