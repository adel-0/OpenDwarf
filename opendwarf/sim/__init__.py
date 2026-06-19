"""DFHack call record/replay tape.

The in-memory simulator (executor/world/scenarios) and the offline eval path
were removed as tautological/false-assurance (origin refactor d23899a). The
recorder is kept on purpose: it is the replay-into-loop harness that refactor
anticipated, used by the playtest skill (`opendwarf.main --record`).
"""

from opendwarf.sim.record import RecordingLuaExecutor, ReplayLuaExecutor

__all__ = [
    "RecordingLuaExecutor",
    "ReplayLuaExecutor",
]
