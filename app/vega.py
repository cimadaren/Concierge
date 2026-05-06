"""
DC Public Library availability client.
Wraps the Innovative Interfaces Vega API at na5.iiivega.com.
"""

import uuid
import httpx

VEGA_BASE = "https://na5.iiivega.com"
SEARCH_PATH = "/api/search-result/search/format-groups"
MAX_AVAILABLE_BRANCHES = 5


def _session_headers() -> dict:
    return {
        "anonymous-user-id": str(uuid.uuid4()),
        "api-version": "2",
        "iii-customer-domain": "dcpl.na5.iiivega.com",
        "iii-host-domain": "catalog.dclibrary.org",
        "accept": "application/json",
        "content-type": "application/json",
    }


def _parse_formats(material_tabs: list) -> list:
    formats = []
    for tab in material_tabs:
        status = tab.get("availability", {}).get("status", {}).get("general", "Unknown")
        locations = tab.get("locations", [])
        available_at = [
            loc["label"]
            for loc in locations
            if loc.get("availabilityStatus") == "Available"
        ]
        fmt = {
            "name": tab["name"],
            "status": status,
            "available_copies": len(available_at),
            "total_branches": tab.get("locationsTotalResults", len(locations)),
            "available_at": available_at[:MAX_AVAILABLE_BRANCHES],
        }
        formats.append(fmt)
    return formats


def _parse_result(item: dict) -> dict:
    tabs = item.get("materialTabs", [])
    record_id = None
    for tab in tabs:
        editions = tab.get("editions", [])
        if editions:
            record_id = editions[0].get("recordId")
            break

    return {
        "title": item.get("title"),
        "author": item.get("primaryAgent", {}).get("label"),
        "year": item.get("publicationDate"),
        "record_id": record_id,
        "formats": _parse_formats(tabs),
    }


async def search(title: str, author: str = "", page_size: int = 1) -> dict | None:
    """
    Search DC Public Library for a title. Returns the top match or None.
    Increase page_size if you want to handle disambiguation.
    """
    payload = {
        "searchText": f"{title} {author}".strip(),
        "sorting": "relevance",
        "sortOrder": "asc",
        "searchType": "everything",
        "pageNum": 0,
        "pageSize": page_size,
        "resourceType": "FormatGroup",
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{VEGA_BASE}{SEARCH_PATH}",
            headers=_session_headers(),
            json=payload,
            timeout=10.0,
        )
        response.raise_for_status()

    data = response.json()
    results = data.get("data", [])
    if not results:
        return None

    return _parse_result(results[0])


async def search_many(books: list[dict]) -> list[dict]:
    """
    Search for multiple books. Each dict must have 'title', optionally 'author'.
    Returns results in the same order; not-found items have status 'NotFound'.
    """
    import asyncio

    async def _search_one(book: dict) -> dict:
        result = await search(book["title"], book.get("author", ""))
        if result is None:
            return {
                "title": book["title"],
                "author": book.get("author"),
                "status": "NotFound",
                "formats": [],
            }
        return result

    return await asyncio.gather(*[_search_one(b) for b in books])
