"""A tiny stdio MCP server for tests/test_agent_loop.py: read, act, fail."""
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("agent")


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def find_lot(project: str) -> str:
    """Read the project's current lot number."""
    return f"The current lot of {project} is 13."


@server.tool()
def launch_lot(project: str, lot: int) -> str:
    """Launch a lot of a project. Acts."""
    return f"launched lot {lot} of {project}"


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def broken(what: str) -> str:
    """Always fails (isError)."""
    raise ValueError("disk on fire")


if __name__ == "__main__":
    server.run("stdio")
