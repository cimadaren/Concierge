"""
Concierge MCP server — exposes DC Public Library search as Claude tools.
Runs locally; configured in ~/.claude/settings.json.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

import vega
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Concierge")


@mcp.tool()
async def search_library(title: str, author: str = "") -> dict:
    """
    Check a single book's availability at DC Public Library.
    Returns availability by format (Book, eBook, Large Print, Audiobook)
    and lists which branches currently have copies on the shelf.
    Use search_library_batch when checking multiple books at once.
    """
    result = await vega.search(title, author)
    if result is None:
        return {"found": False, "title": title, "author": author}
    return {"found": True, **result}


@mcp.tool()
async def search_library_batch(books: list[dict]) -> list[dict]:
    """
    Check availability for a list of books at DC Public Library.
    Each item must have a 'title' key and optionally an 'author' key.
    Use this when the user wants to check a set of recommendations —
    it runs all lookups concurrently and returns results in the same order.

    Example input:
      [{"title": "Project Hail Mary", "author": "Andy Weir"},
       {"title": "Fourth Wing"}]
    """
    return await vega.search_many(books)


if __name__ == "__main__":
    mcp.run()
