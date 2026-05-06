# Concierge — Architecture

Personal LLM assistant that checks book availability at DC Public Library and movie streaming availability — whatever you're asking an LLM about tonight, Concierge finds out if you can actually get it.

---

## Problem

LLM book recommendations require manually searching the DC Public Library catalog one book at a time. 90% aren't available; the rest need holds placed. This automates the entire lookup (and eventually hold placement).

---

## DC Public Library API

The catalog at `catalog.dclibrary.org` runs on **Innovative Interfaces Vega**, hosted at `na5.iiivega.com`. Reverse-engineered 2026-05-01 via browser DevTools network inspection.

### Authentication

No real auth for public search. Every request requires these headers:

```
anonymous-user-id: <any UUID v4>
api-version: 2
iii-customer-domain: dcpl.na5.iiivega.com
iii-host-domain: catalog.dclibrary.org
accept: application/json
content-type: application/json
```

Generate a fresh UUID per session. The `anonymous-user-id` is a client-generated tracking identifier — any valid UUID works.

### Search Endpoint

```
POST https://na5.iiivega.com/api/search-result/search/format-groups
```

Request body:
```json
{
  "searchText": "Project Hail Mary Andy Weir",
  "sorting": "relevance",
  "sortOrder": "asc",
  "searchType": "everything",
  "pageNum": 0,
  "pageSize": 10,
  "resourceType": "FormatGroup"
}
```

Response includes per result:
- Title, author, publication date, cover image URLs
- `materialTabs[]` — one per format: Book, eBook, Large Print, Audiobook, eAudiobook
  - `availability.status.general`: `Available` | `CheckedOut` | `CheckAvailability`
  - `locations[]`: per-branch name + `availabilityStatus`
  - `locationsTotalResults`: total branch count holding this format
  - `editions[].recordId`: Sierra bib record ID (needed for placing holds)
  - `identifiedBy.isbn[]`: ISBNs

### Autocomplete / Suggestions

```
GET https://na5.iiivega.com/api/search/suggestions?phrase=...
api-version: 1
```

Returns `FormatGroup` ID and matched term. Useful for exact-match disambiguation.

### Patron Auth / Holds

Not yet reverse-engineered. Requires library card number + PIN. Pattern follows the same header convention. Patron credentials are stored in SSM Parameter Store — never passed through the public API surface.

---

## Phase 1 — AWS Serverless API

### Stack

| Component | Choice | Why |
|---|---|---|
| Runtime | Python 3.12 | Proven Vega API code is Python |
| Framework | FastAPI + Mangum | Auto-generates OpenAPI spec; Mangum adapts ASGI to Lambda |
| Compute | AWS Lambda | Pay-per-request, zero idle cost |
| API layer | API Gateway HTTP API v2 | 70% cheaper than REST API v1; simpler config |
| Secrets | SSM Parameter Store (Standard) | Free tier; sufficient for card + PIN |
| IaC | Terraform | Daren's established tooling |
| Logs | CloudWatch Logs | Default Lambda integration |

### Request Flow

```
LLM / curl / web client
        │
        ▼
API Gateway HTTP API v2
  POST /search
  POST /hold
  GET  /patron
        │  Lambda proxy integration
        ▼
Lambda Function (Python 3.12)
  FastAPI app via Mangum adapter
  - Reads SSM params at cold start
  - Calls na5.iiivega.com API
  - Returns structured availability data
        │
        ▼
na5.iiivega.com  (Vega API, no auth)
SSM Parameter Store (patron credentials)
```

### API Endpoints

#### `POST /search`
Search for one or more books and return availability.

Request:
```json
{
  "books": [
    { "title": "Project Hail Mary", "author": "Andy Weir" },
    { "title": "Fourth Wing" }
  ]
}
```

Response:
```json
{
  "results": [
    {
      "title": "Project Hail Mary",
      "author": "Weir, Andy",
      "year": "2021",
      "formats": [
        {
          "name": "Book",
          "status": "CheckedOut",
          "available_copies": 0,
          "total_branches": 18,
          "available_at": []
        },
        {
          "name": "eBook",
          "status": "CheckAvailability"
        }
      ],
      "record_id": "263377"
    }
  ]
}
```

#### `POST /hold`
Place a hold on a specific record. Patron credentials injected server-side from SSM.

```json
{ "record_id": "263377", "format": "Book" }
```

#### `GET /patron`
Return current holds queue and checked-out items for the configured patron.

### Cost Estimate

Personal use (~500 requests/month):

| Service | Free Tier | Estimated Monthly Cost |
|---|---|---|
| Lambda | 1M req, 400K GB-sec/mo | $0 |
| API Gateway HTTP API v2 | 1M req/mo (12 mo) | $0 → ~$0.001 |
| SSM Parameter Store | Standard params free | $0 |
| CloudWatch Logs | 5 GB/mo | $0 |
| **Total** | | **~$0/month** |

Even at 10,000 requests/month: < $0.05.

### Terraform Resources

```
aws_lambda_function
aws_lambda_permission          (allow API GW to invoke)
aws_apigatewayv2_api           (HTTP API)
aws_apigatewayv2_integration
aws_apigatewayv2_route         (POST /search, POST /hold, GET /patron)
aws_apigatewayv2_stage         ($default, auto-deploy)
aws_iam_role                   (Lambda execution role)
aws_iam_role_policy            (SSM read + CloudWatch logs)
aws_ssm_parameter              (card_number, pin)
aws_cloudwatch_log_group
```

### OpenAPI / LLM Integration

FastAPI auto-generates OpenAPI 3.0 spec at `/openapi.json`. Use this to:
- **ChatGPT Custom Action**: paste the API URL into GPT builder → ChatGPT can call `/search` mid-conversation
- **Any tool-calling LLM**: standard OpenAPI tool definition

---

## Phase 2 — MCP Server (Claude-native)

An MCP server lets Claude call library tools natively during any conversation, without a deployed API.

### How It Works

1. MCP server runs locally on WSL2 as a subprocess
2. Configured in `~/.claude/settings.json`
3. Claude Code sees `search_library` and `check_availability` as built-in tools
4. During a book discussion: *"check availability of your recommendations"* → Claude calls the tool automatically

### Stack

- Python `mcp` SDK with `FastMCP` (same pattern as FastAPI, ~40 lines)
- Calls Vega API directly (no Lambda hop needed for local use)
- Patron credentials in local env vars or `~/.claude/settings.json` env block

### Example Tool Definition

```python
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("DC Library")

@mcp.tool()
def search_library(title: str, author: str = "") -> dict:
    """Check availability of a book at DC Public Library across all branches and formats."""
    # ... Vega API call ...
```

### Configuration (`~/.claude/settings.json`)

```json
{
  "mcpServers": {
    "dc-library": {
      "command": "python3",
      "args": ["/home/daren/Personal/projects/LibraryBooks/mcp_server.py"],
      "env": {
        "DCPL_CARD": "...",
        "DCPL_PIN": "..."
      }
    }
  }
}
```

---

## Phase 3 — Movie Recommendations (Nice to Have)

Same Lambda, same pattern — two additional external APIs, one new endpoint.

### Use Case

> *"Give me 5 sci-fi movie recommendations"*
> *"Check scores and streaming availability for those"*
> → Claude calls `search_movies()` for each and returns a consolidated report

The key value isn't review scores (LLMs already know those from training data) — it's **current streaming availability**, which changes constantly and isn't in any model's training data.

### External APIs

**OMDb API** (`omdbapi.com`) — ratings aggregator
- Single call returns IMDB rating, Rotten Tomatoes %, and Metacritic score
- Free tier: 1,000 req/day (personal use: more than sufficient)
- Auth: API key as query param (`?apikey=...`)
- Cost after free tier: ~$1/month for 100K requests

**Watchmode API** (`api.watchmode.com`) — streaming availability
- Two-call pattern: search by title → get streaming sources by Watchmode ID
- Returns subscription, rental, free, and TV sources per country
- Free tier: 2,500 req/month
- Cost after free tier: ~$9/month (likely overkill for personal use)
- Auth: API key as query param (`?apiKey=...`)

### Call Chain (example: "The Martian")

```
1. OMDb:     GET /?t=The+Martian&type=movie&apikey=...
             → { imdb: "8.0", rottenTomatoes: "91%", metacritic: "80" }

2. Watchmode: GET /v1/search/?search_field=name&search_value=The+Martian
             → { id: 1234567, ... }

3. Watchmode: GET /v1/title/1234567/sources/
             → [{ name: "Max", type: "sub" },
                { name: "Hulu", type: "sub" },
                { name: "Apple TV", type: "rent" }, ...]
```

### New Endpoint

#### `POST /movies/search`

Request:
```json
{
  "movies": [
    { "title": "The Martian", "year": 2015 },
    { "title": "Interstellar" }
  ]
}
```

Response:
```json
{
  "results": [
    {
      "title": "The Martian",
      "year": "2015",
      "director": "Ridley Scott",
      "ratings": {
        "imdb": "8.0",
        "rotten_tomatoes": "91%",
        "metacritic": "80"
      },
      "streaming": [
        { "service": "Max", "type": "subscription" },
        { "service": "Hulu", "type": "subscription" }
      ],
      "rental": [
        { "service": "Apple TV", "type": "rent" },
        { "service": "Amazon", "type": "rent" }
      ]
    }
  ]
}
```

### Additional SSM Parameters

```
/librarybooks/omdb_api_key
/librarybooks/watchmode_api_key
```

### Cost Addition

| Service | Free Tier | Est. Monthly Cost |
|---|---|---|
| OMDb | 1,000 req/day | $0 |
| Watchmode | 2,500 req/month | $0 → ~$9 if exceeded |
| **Phase 3 addition** | | **$0–$9/month** |

---

## Build Order

### Phase 1
- [ ] `app/main.py` — FastAPI app with `/search`, local `uvicorn` dev
- [ ] `app/vega.py` — Vega API client (from proven POC code)
- [ ] Add Mangum adapter, test Lambda handler locally
- [ ] `terraform/` — Lambda + API GW + SSM + IAM
- [ ] Deploy to AWS, validate end-to-end
- [ ] Reverse-engineer Vega patron auth → add `/hold`, `/patron`

### Phase 2
- [ ] `mcp_server.py` — FastMCP server wrapping Vega client
- [ ] Configure in `~/.claude/settings.json`
- [ ] Test mid-conversation tool use in Claude Code

### Phase 3
- [ ] Sign up for OMDb and Watchmode free-tier API keys
- [ ] Store keys in SSM (`/librarybooks/omdb_api_key`, `/librarybooks/watchmode_api_key`)
- [ ] `app/omdb.py` — OMDb client
- [ ] `app/watchmode.py` — Watchmode client (search + sources)
- [ ] Add `POST /movies/search` endpoint to FastAPI app
- [ ] Add `search_movies` tool to MCP server

---

## Repository Structure (planned)

```
Concierge/
├── ARCHITECTURE.md         (this file)
├── app/
│   ├── main.py             FastAPI app + Mangum handler
│   ├── vega.py             DC Public Library Vega API client
│   ├── omdb.py             Phase 3: OMDb ratings client
│   ├── watchmode.py        Phase 3: Watchmode streaming client
│   └── models.py           Pydantic request/response models
├── mcp_server.py           Phase 2: FastMCP server
├── template.yaml           SAM template (Lambda + API GW + IAM)
├── samconfig.toml          SAM deploy config (profile=personal locked in)
├── tests/
│   └── test_vega.py
└── requirements.txt
```
