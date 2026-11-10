"""A tiny local MCP server (stdio) used by the MCP client tests. Not part of the platform."""
from mcp.server.fastmcp import FastMCP

srv = FastMCP("echo-test")


@srv.tool(description="Echo the text back.")
def echo(text: str) -> str:
    return f"echo: {text}"


@srv.tool(description="Add two integers.")
def add(a: int, b: int) -> int:
    return a + b


@srv.resource("notes://readme", description="A static note.")
def readme() -> str:
    return "hello from the test server"


if __name__ == "__main__":
    srv.run("stdio")
