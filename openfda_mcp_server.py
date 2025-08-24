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

# Add this helper function for debugging
async def _fetch_openfda_with_logging(params: dict) -> dict:
    """Enhanced version with logging for debugging NDC searches"""
    log.info(f"FDA API query: {params}")
    
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(OPENFDA_URL, params=params)
        if r.status_code == 404:
            log.info(f"No results found for query: {params['search']}")
            return {"results": []}
        r.raise_for_status()
        result = r.json()
        log.info(f"Found {len(result.get('results', []))} results")
        return result
        
# ── FIXED: Simplified NDC format handler ─────────────────────────────────────────
def _normalize_ndc(ndc_input: str) -> List[str]:
    """
    Simplified NDC format handler - only try the most common valid formats.
    This fixes the complex query issue that was causing fallback results.
    """
    if not ndc_input:
        return []
    
    ndc_input = ndc_input.strip()
    formats = [ndc_input]
    
    # If it has hyphens, also try without hyphens
    if '-' in ndc_input:
        clean_ndc = re.sub(r'[^\d]', '', ndc_input)
        if len(clean_ndc) >= 9:  # Valid NDC should be at least 9 digits
            formats.append(clean_ndc)
    else:
        # If no hyphens and looks like a valid NDC, try adding standard hyphenation
        clean_ndc = re.sub(r'[^\d]', '', ndc_input)
        if len(clean_ndc) == 10:
            # Try 5-4-1 format (most common)
            formats.append(f"{clean_ndc[:5]}-{clean_ndc[5:9]}-{clean_ndc[9:]}")
        elif len(clean_ndc) == 11:
            # Try 5-4-2 format (most common)
            formats.append(f"{clean_ndc[:5]}-{clean_ndc[5:9]}-{clean_ndc[9:]}")
    
    # Return only unique, valid formats (max 3 to keep query simple)
    return list(dict.fromkeys(formats))[:3]


# ── UPDATED: Better search builder ───────────────────────────────────────────
def _build_search(
    drug: Optional[str],
    manufacturer: Optional[str] = None,
    dosage_form: Optional[str] = None,
    route: Optional[str] = None,
    ndc: Optional[str] = None,
    exact: bool = False
) -> str:
    # If NDC is provided, prioritize it as the primary search
    if ndc:
        ndc_formats = _normalize_ndc(ndc)
        if ndc_formats:
            # Try all possible NDC formats
            ndc_queries = []
            for ndc_format in ndc_formats:
                ndc_queries.append(f'openfda.product_ndc:"{ndc_format}"')
            
            ndc_query = "(" + " OR ".join(ndc_queries) + ")"
            
            # For NDC searches, only add other filters if they're provided
            # and combine them more carefully
            additional_filters = []
            
            if manufacturer:
                additional_filters.append(f'openfda.manufacturer_name:"{manufacturer}"')
            if dosage_form:
                additional_filters.append(f'openfda.dosage_form:"{dosage_form}"')
            if route:
                additional_filters.append(f'openfda.route:"{route}"')
            
            # If no additional filters, return NDC query only
            if not additional_filters and not drug:
                return ndc_query
            
            # Combine NDC with other filters
            query_parts = [ndc_query]
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
            
            query_parts.extend(additional_filters)
            return " AND ".join(query_parts)
    
    # Non-NDC searches (original logic)
    query_parts = []
    
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
    data = await _fetch_openfda_with_logging(params)
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
        data = await _fetch_openfda_with_logging(params)
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
