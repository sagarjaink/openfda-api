"""
Remote MCP server that exposes one tool:

    get_drug_indications(drug_name: str,
                         limit: int = 3,
                         exact_match: bool = False)
→ Returns FDA-approved “indications & usage” text, plus metadata.

Transport: Streamable-HTTP  (Claude-compatible)
Path:      /mcp
"""

import os, httpx, logging, asyncio
from datetime import datetime
from typing import List
from pydantic import BaseModel, Field
from fastmcp import FastMCP

# ── basic setup ───────────────────────────────────────────────────────────────
from fastmcp import FastMCP

mcp = FastMCP(
    "OpenFDA Tools",
    instructions="Query FDA drug-label data in real time"
)
log = logging.getLogger("openfda_mcp")

OPENFDA_URL = "https://api.fda.gov/drug/label.json"    # official endpoint
TIMEOUT      = 20

# ── Structured output schema (nice for Claude) ───────────────────────────────
class DrugInfo(BaseModel):
    brand_names:   List[str] = Field(..., alias="brandNames")
    generic_names: List[str] = Field(..., alias="genericNames")
    manufacturer:  List[str]
    indications:   List[str]
    ndc_codes:     List[str] = Field(..., alias="ndcCodes")

# ── Helper to hit OpenFDA ─────────────────────────────────────────────────────
async def _fetch_openfda(params: dict) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(OPENFDA_URL, params=params)
        if r.status_code == 404:
            return {"results": []}
        r.raise_for_status()
        return r.json()

def _build_search(drug: str, exact: bool) -> str:
    if exact:
        return (f'openfda.brand_name.exact:"{drug}" OR '
                f'openfda.generic_name.exact:"{drug}"')
    return (f'openfda.brand_name:"{drug}" OR '
            f'openfda.generic_name:"{drug}" OR '
            f'openfda.substance_name:"{drug}"')

# ── The MCP tool Claude will call ────────────────────────────────────────────
@mcp.tool(
    name="get_drug_indications",
    description="Returns FDA-approved ‘Indications & Usage’ text plus metadata."
)
async def get_drug_indications(
    drug_name: str,
    limit: int = 3,
    exact_match: bool = False
) -> List[DrugInfo]:
    """
    drug_name   – brand, generic or ingredient name
    limit       – max number of label records (1-10)
    exact_match – if True, search only exact brand/generic
    """
    params = {"search": _build_search(drug_name.strip(), exact_match),
              "limit":  max(1, min(limit, 10))}
    log.info("OpenFDA params: %s", params)

    data = await _fetch_openfda(params)
    if not data.get("results"):
        return []

    out: List[DrugInfo] = []
    for rec in data["results"]:
        ofda = rec.get("openfda", {})
        out.append(DrugInfo(
            brandNames   = ofda.get("brand_name", []),
            genericNames = ofda.get("generic_name", []),
            manufacturer = ofda.get("manufacturer_name", []),
            indications  = rec.get("indications_and_usage", []),
            ndcCodes     = ofda.get("product_ndc", []),
        ))
    return out

# ── Entrypoint (Render/railway/etc. call this) ───────────────────────────────
if __name__ == "__main__":
    # Claude expects either SSE or Streamable-HTTP; FastMCP’s “http” transport
    # implements the *new* Streamable-HTTP spec :contentReference[oaicite:0]{index=0}
    port = int(os.getenv("PORT", "8000"))
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=port,
        path="/mcp",          # this becomes your Claude “Base URL”
        log_level="info"
    )
