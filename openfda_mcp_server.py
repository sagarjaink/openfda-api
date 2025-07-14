"""
Remote MCP server that exposes 7 tools for querying FDA drug label data by drug name, NDC, manufacturer, dosage form, or route.
Transport: Streamable-HTTP  (Claude-compatible)
Path:      /mcp
"""

import os, httpx, logging, asyncio, re
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field
from fastmcp import FastMCP

# ── basic setup ───────────────────────────────────────────────────────────────
mcp = FastMCP(
    "OpenFDA Tools",
    instructions="Query FDA drug-label data in real time"
)
log = logging.getLogger("openfda_mcp")

OPENFDA_URL = "https://api.fda.gov/drug/label.json"
TIMEOUT = 20

# ── Structured output schema ──────────────────────────────────────────────────
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

# ── NEW: Smart NDC format handler ─────────────────────────────────────────────
def _normalize_ndc(ndc_input: str) -> List[str]:
    """
    Convert NDC input into possible valid formats.
    NDC can be 10 or 11 digits in 3 segments with various patterns.
    """
    if not ndc_input:
        return []
    
    # Clean input - remove spaces, hyphens, and normalize
    clean_ndc = re.sub(r'[^\d]', '', ndc_input.strip())
    
    # Validate basic format (10 or 11 digits)
    if not re.match(r'^\d{10,11}$', clean_ndc):
        return []
    
    possible_formats = set()
    
    # Add the cleaned version (no hyphens)
    possible_formats.add(clean_ndc)
    
    # If original had hyphens, keep that format too
    if '-' in ndc_input:
        possible_formats.add(ndc_input.strip())
    
    # Generate common NDC formats based on length
    if len(clean_ndc) == 10:
        # 10-digit NDC patterns
        possible_formats.add(f"{clean_ndc[:4]}-{clean_ndc[4:7]}-{clean_ndc[7:]}")  # 4-3-3
        possible_formats.add(f"{clean_ndc[:5]}-{clean_ndc[5:8]}-{clean_ndc[8:]}")  # 5-3-2
        possible_formats.add(f"{clean_ndc[:5]}-{clean_ndc[5:7]}-{clean_ndc[7:]}")  # 5-2-3
    elif len(clean_ndc) == 11:
        # 11-digit NDC patterns  
        possible_formats.add(f"{clean_ndc[:5]}-{clean_ndc[5:9]}-{clean_ndc[9:]}")  # 5-4-2
        possible_formats.add(f"{clean_ndc[:5]}-{clean_ndc[5:8]}-{clean_ndc[8:]}")  # 5-3-3
        possible_formats.add(f"{clean_ndc[:4]}-{clean_ndc[4:8]}-{clean_ndc[8:]}")  # 4-4-3
    
    return list(possible_formats)

# ── UPDATED: Better search builder ───────────────────────────────────────────
def _build_search(
    drug: Optional[str],
    manufacturer: Optional[str] = None,
    dosage_form: Optional[str] = None,
    route: Optional[str] = None,
    ndc: Optional[str] = None,
    exact: bool = False
) -> str:
    query_parts = []

    # Handle NDC queries with multiple format attempts
    if ndc:
        ndc_formats = _normalize_ndc(ndc)
        if ndc_formats:
            # Try all possible NDC formats
            ndc_queries = []
            for ndc_format in ndc_formats:
                ndc_queries.append(f'openfda.product_ndc:"{ndc_format}"')
            
            # If NDC is provided and valid, make it the primary search
            # but still allow additional filters
            ndc_query = "(" + " OR ".join(ndc_queries) + ")"
            query_parts.append(ndc_query)
    
    # Add drug name query if provided
    if drug:
        fields = [
            "openfda.brand_name",
            "openfda.generic_name", 
            "openfda.substance_name"
        ]
        drug_query = "(" + " OR ".join(
            f'{field}.exact:"{drug}"' if exact else f'{field}:"{drug}"'
            for field in fields
        ) + ")"
        query_parts.append(drug_query)

    # Add other filters
    if manufacturer:
        query_parts.append(f'openfda.manufacturer_name:"{manufacturer}"')
    if dosage_form:
        query_parts.append(f'openfda.dosage_form:"{dosage_form}"')
    if route:
        query_parts.append(f'openfda.route:"{route}"')

    return " AND ".join(query_parts) if query_parts else "*:*"

# ── Tools ─────────────────────────────────────────────────────────────────────
@mcp.tool(
    name="get_drug_indications",
    description="Returns FDA-approved indications. Supports filtering by drug name, NDC, manufacturer, dosage form, and route."
)
async def get_drug_indications(
    drug_name: Optional[str] = None,
    manufacturer: Optional[str] = None,
    dosage_form: Optional[str] = None,
    route: Optional[str] = None,
    ndc: Optional[str] = None,
    limit: int = 3,
    exact_match: bool = False
) -> List[DrugInfo]:
    params = {"search": _build_search(drug_name, manufacturer, dosage_form, route, ndc, exact_match),
              "limit": max(1, min(limit, 10))}
    data = await _fetch_openfda(params)
    if not data.get("results"):
        return []
    out: List[DrugInfo] = []
    for rec in data["results"]:
        ofda = rec.get("openfda", {})
        out.append(DrugInfo(
            brandNames=ofda.get("brand_name", []),
            genericNames=ofda.get("generic_name", []),
            manufacturer=ofda.get("manufacturer_name", []),
            indications=rec.get("indications_and_usage", []),
            ndcCodes=ofda.get("product_ndc", []),
        ))
    return out

# ── One-template tools for other sections ─────────────────────────────────────
def make_simple_tool(section: str, tool_name: str, description: str):
    @mcp.tool(name=tool_name, description=description)
    async def tool(
        drug_name: Optional[str] = None,
        manufacturer: Optional[str] = None,
        dosage_form: Optional[str] = None,
        route: Optional[str] = None,
        ndc: Optional[str] = None,
        limit: int = 3,
        exact_match: bool = False
    ) -> List[str]:
        params = {"search": _build_search(drug_name, manufacturer, dosage_form, route, ndc, exact_match),
                  "limit": max(1, min(limit, 10))}
        data = await _fetch_openfda(params)
        if not data.get("results"):
            return []
        out: List[str] = []
        for rec in data["results"]:
            section_data = rec.get(section, [])
            out.extend(section_data)
        return out
    return tool

# Registering all tools
get_drug_dosage = make_simple_tool(
    "dosage_and_administration",
    "get_drug_dosage",
    "Returns FDA-approved dosage and administration instructions. Supports filtering by drug name, NDC, manufacturer, dosage form, and route."
)

get_specific_populations = make_simple_tool(
    "use_in_specific_populations",
    "get_specific_populations",
    "Returns FDA 'Use in Specific Populations' info. Supports filtering by drug name, NDC, manufacturer, dosage form, and route."
)

get_storage_handling = make_simple_tool(
    "how_supplied_storage_and_handling",
    "get_storage_handling",
    "Returns FDA 'How Supplied/Storage and Handling' info. Supports filtering by drug name, NDC, manufacturer, dosage form, and route."
)

get_warnings_precautions = make_simple_tool(
    "warnings_and_precautions",
    "get_warnings_precautions",
    "Returns FDA 'Warnings and Precautions' info. Supports filtering by drug name, NDC, manufacturer, dosage form, and route."
)

get_clinical_pharmacology = make_simple_tool(
    "clinical_pharmacology",
    "get_clinical_pharmacology",
    "Returns FDA 'Clinical Pharmacology' info. Supports filtering by drug name, NDC, manufacturer, dosage form, and route."
)

get_drug_description = make_simple_tool(
    "description",
    "get_drug_description",
    "Returns FDA-approved product description. Supports filtering by drug name, NDC, manufacturer, dosage form, and route."
)

# ── Entrypoint ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=port,
        path="/mcp",
        log_level="info"
    )
