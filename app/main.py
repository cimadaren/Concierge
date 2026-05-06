from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from mangum import Mangum
import vega

app = FastAPI(
    title="Concierge",
    description="Check DC Public Library book availability — callable by any LLM.",
    version="0.1.0",
)


class Book(BaseModel):
    title: str
    author: str = ""


class SearchRequest(BaseModel):
    books: list[Book]


class FormatAvailability(BaseModel):
    name: str
    status: str
    available_copies: int
    total_branches: int
    available_at: list[str]


class BookResult(BaseModel):
    title: str | None
    author: str | None
    year: str | None
    record_id: str | None
    formats: list[FormatAvailability]


class SearchResponse(BaseModel):
    results: list[BookResult]


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    if not request.books:
        raise HTTPException(status_code=400, detail="No books provided.")
    if len(request.books) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 books per request.")

    raw = await vega.search_many([b.model_dump() for b in request.books])
    return SearchResponse(results=raw)


# Lambda entry point
handler = Mangum(app)
