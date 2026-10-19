"""Fake MCP servers for tests/bench_mcp.py: ``python mcp_bench_server.py board|ha``.

Every call is appended to the file named by BENCH_LOG (one JSON line: tool,
whether it acts, whether it worked) — the bench reads what really ran from
there, never from what the model says it did.
"""
import functools
import json
import os
import sys

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

kind = sys.argv[1] if len(sys.argv) > 1 else "board"
server = MCPServer(kind)


def tool(acts=True):
    """Register a tool; unannotated = acts (what mcp_tools assumes), readOnlyHint otherwise."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapped(*a, **kw):
            ok = True
            try:
                return fn(*a, **kw)
            except Exception:
                ok = False
                raise
            finally:
                with open(os.environ["BENCH_LOG"], "a") as f:
                    f.write(json.dumps({"tool": fn.__name__, "acts": acts, "ok": ok}) + "\n")
        return server.tool(annotations=None if acts else ToolAnnotations(read_only_hint=True))(wrapped)
    return deco


if kind == "board":
    @tool()
    def launch_lot(project: str, lot: int) -> str:
        """Launch a lot of a project as real agents. Acts."""
        if project != "loquivox":
            raise ValueError(f"unknown project {project}")
        return f"launched lot {lot} of {project}"

    @tool()
    def finish_card(card: int) -> str:
        """Mark a card as finished. Acts."""
        return f"card {card} finished"

    @tool(False)
    def list_projects() -> str:
        """List the board's projects."""
        return "Projects: loquivox"

    @tool(False)
    def find_lot(project: str) -> str:
        """Read the project's current lot number."""
        return f"The current lot of {project} is 13."

    @tool(False)
    def project_status(project: str) -> str:
        """Read a project's status: lot, cards in progress."""
        return f"{project}: lot 13, 4 cards in progress, 1 blocked."

    @tool(False)
    def whats_blocking() -> str:
        """Say which cards are blocked and why."""
        return "Card 42 is blocked: waiting for a review."

    @tool(False)
    def export_report(project: str) -> str:
        """Export the project's report. Always fails."""
        raise ValueError("disk on fire")
else:
    @tool()
    def light_turn_on(entity_id: str) -> str:
        """Turn a light on (entity ids look like light.salon). Acts."""
        return f"{entity_id} is on"

    @tool()
    def light_turn_off(entity_id: str) -> str:
        """Turn a light off (entity ids look like light.salon). Acts."""
        return f"{entity_id} is off"

    @tool(False)
    def get_state(entity_id: str) -> str:
        """Read an entity's state (light.salon, sensor.temperature_salon)."""
        return {"sensor.temperature_salon": "21.5 °C"}.get(entity_id, f"{entity_id}: off")

if __name__ == "__main__":
    server.run("stdio")
