from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional
import httpx
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("openfda-api")

OPENFDA_BASE_URL = "https://api.fda.gov"
DRUG_LABEL_ENDPOINT = f"{OPENFDA_BASE_URL}/drug/label.json"
REQUEST_TIMEOUT = 30

app = FastAPI(
    title="OpenFDA Drug Indications API",
    description="Get FDA drug indications for Claude.ai",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class DrugInfo(BaseModel):
    brand_names: List[str]
    generic_names: List[str]
    manufacturer: List[str]
    indications_and_usage: List[str]
    ndc_codes: List[str]

class DrugIndicationsResponse(BaseModel):
    success: bool
    query: str
    total_results: int
    results: List[DrugInfo]
    timestamp: str

@app.get("/")
async def root():
    return {
        "message": "OpenFDA Drug Indications API",
        "version": "1.0.0",
        "example": "/drug/indications?drug_name=aspirin&limit=1"
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/drug/indications", response_model=DrugIndicationsResponse)
async def get_drug_indications(
    drug_name: str = Query(..., description="Drug name to search for"),
    limit: int = Query(default=3, ge=1, le=10, description="Max results"),
    exact_match: bool = Query(default=False, description="Use exact matching")
):
    timestamp = datetime.now().isoformat()
    
    try:
        if exact_match:
            search_query = f'openfda.brand_name.exact:"{drug_name}" OR openfda.generic_name.exact:"{drug_name}"'
        else:
            search_query = f'openfda.brand_name:"{drug_name}" OR openfda.generic_name:"{drug_name}" OR openfda.substance_name:"{drug_name}"'
        
        params = {"search": search_query, "limit": min(limit, 10)}
        
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(DRUG_LABEL_ENDPOINT, params=params)
            response.raise_for_status()
            
            data = response.json()
            
            if "results" not in data or not data["results"]:
                return DrugIndicationsResponse(
                    success=True, query=drug_name, total_results=0, 
                    results=[], timestamp=timestamp
                )
            
            processed_results = []
            for result in data["results"]:
                brand_names = result.get("openfda", {}).get("brand_name", [])
                generic_names = result.get("openfda", {}).get("generic_name", [])
                manufacturer = result.get("openfda", {}).get("manufacturer_name", [])
                ndc_codes = result.get("openfda", {}).get("product_ndc", [])
                indications = result.get("indications_and_usage", [])
                
                cleaned_indications = []
                for indication in indications[:2]:
                    clean_indication = " ".join(indication.split())
                    if len(clean_indication) > 1000:
                        clean_indication = clean_indication[:1000] + "..."
                    cleaned_indications.append(clean_indication)
                
                drug_info = DrugInfo(
                    brand_names=brand_names[:3],
                    generic_names=generic_names[:3],
                    manufacturer=manufacturer[:2],
                    indications_and_usage=cleaned_indications,
                    ndc_codes=ndc_codes[:5]
                )
                processed_results.append(drug_info)
            
            return DrugIndicationsResponse(
                success=True, query=drug_name, total_results=len(processed_results),
                results=processed_results, timestamp=timestamp
            )
            
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
