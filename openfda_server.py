import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("openfda-mcp")

# OpenFDA API configuration
OPENFDA_BASE_URL = "https://api.fda.gov"
DRUG_LABEL_ENDPOINT = f"{OPENFDA_BASE_URL}/drug/label.json"
REQUEST_TIMEOUT = 30

app = FastAPI(
    title="OpenFDA MCP Server",
    description="Model Context Protocol server for OpenFDA drug information",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MCP Protocol Classes
class MCPMessage(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[Union[str, int]] = None
    method: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None

class MCPTool(BaseModel):
    name: str
    description: str
    inputSchema: Dict[str, Any]

class MCPInitializeResult(BaseModel):
    protocolVersion: str
    capabilities: Dict[str, Any]
    serverInfo: Dict[str, str]

# MCP Server State
class MCPServer:
    def __init__(self):
        self.tools = [
            MCPTool(
                name="get_drug_indications",
                description="Get FDA-approved indications and usage information for a drug",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "drug_name": {
                            "type": "string",
                            "description": "Name of the drug to search for (brand name or generic name)"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results to return (default: 3, max: 10)",
                            "default": 3,
                            "minimum": 1,
                            "maximum": 10
                        },
                        "exact_match": {
                            "type": "boolean",
                            "description": "Whether to search for exact matches only (default: false)",
                            "default": False
                        }
                    },
                    "required": ["drug_name"]
                }
            )
        ]
    
    async def handle_initialize(self, params: Dict[str, Any]) -> MCPInitializeResult:
        """Handle MCP initialize request"""
        return MCPInitializeResult(
            protocolVersion="2024-11-05",
            capabilities={
                "tools": {}
            },
            serverInfo={
                "name": "openfda-mcp-server",
                "version": "1.0.0"
            }
        )
    
    async def handle_list_tools(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Handle tools/list request"""
        return [tool.model_dump() for tool in self.tools]
    
    async def handle_call_tool(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tools/call request"""
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        
        if tool_name == "get_drug_indications":
            return await self.get_drug_indications(arguments)
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
    
    async def get_drug_indications(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Get drug indications from OpenFDA"""
        drug_name = arguments.get("drug_name", "").strip()
        limit = arguments.get("limit", 3)
        exact_match = arguments.get("exact_match", False)
        
        if not drug_name:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: drug_name parameter is required"
                }]
            }
        
        try:
            # Build search query
            if exact_match:
                search_query = f'openfda.brand_name.exact:"{drug_name}" OR openfda.generic_name.exact:"{drug_name}"'
            else:
                search_query = f'openfda.brand_name:"{drug_name}" OR openfda.generic_name:"{drug_name}" OR openfda.substance_name:"{drug_name}"'
            
            params = {
                "search": search_query,
                "limit": min(limit, 10)
            }
            
            logger.info(f"Searching OpenFDA for drug: {drug_name}")
            
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.get(DRUG_LABEL_ENDPOINT, params=params)
                response.raise_for_status()
                
                data = response.json()
                
                if "results" not in data or not data["results"]:
                    return {
                        "content": [{
                            "type": "text",
                            "text": f"No FDA labeling data found for drug: '{drug_name}'"
                        }]
                    }
                
                # Format results for MCP
                result_text = f"Found {len(data['results'])} FDA drug label(s) for '{drug_name}':\n\n"
                
                for i, result in enumerate(data["results"], 1):
                    # Extract drug identification info
                    brand_names = result.get("openfda", {}).get("brand_name", ["Unknown"])
                    generic_names = result.get("openfda", {}).get("generic_name", ["Unknown"])
                    manufacturer = result.get("openfda", {}).get("manufacturer_name", ["Unknown"])
                    
                    # Extract indications and usage
                    indications = result.get("indications_and_usage", [])
                    
                    result_text += f"--- Result {i} ---\n"
                    result_text += f"Brand Name(s): {', '.join(brand_names[:3])}\n"
                    result_text += f"Generic Name(s): {', '.join(generic_names[:3])}\n"
                    result_text += f"Manufacturer: {', '.join(manufacturer[:2])}\n"
                    
                    if indications:
                        result_text += f"\nINDICATIONS AND USAGE:\n"
                        for indication in indications[:2]:  # Limit to first 2 sections
                            clean_indication = " ".join(indication.split())
                            if len(clean_indication) > 1000:
                                clean_indication = clean_indication[:1000] + "..."
                            result_text += f"{clean_indication}\n\n"
                    else:
                        result_text += f"\nINDICATIONS AND USAGE: Not available in label\n"
                    
                    # Add NDC codes
                    ndc_codes = result.get("openfda", {}).get("product_ndc", [])
                    if ndc_codes:
                        result_text += f"NDC Code(s): {', '.join(ndc_codes[:3])}\n"
                    
                    result_text += "\n"
                
                return {
                    "content": [{
                        "type": "text",
                        "text": result_text
                    }]
                }
                
        except Exception as e:
            logger.error(f"Error getting drug indications: {str(e)}")
            return {
                "content": [{
                    "type": "text",
                    "text": f"Error: {str(e)}"
                }]
            }

# Global MCP server instance
mcp_server = MCPServer()

@app.get("/")
async def root():
    """Root endpoint with MCP server information"""
    return {
        "name": "OpenFDA MCP Server",
        "version": "1.0.0",
        "description": "Model Context Protocol server for OpenFDA drug information",
        "mcp_endpoint": "/sse",
        "tools": ["get_drug_indications"],
        "example_connection": "Add 'https://your-url/sse' as custom integration in Claude.ai"
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "OpenFDA MCP Server"
    }

async def handle_mcp_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """Handle incoming MCP message"""
    try:
        method = message.get("method")
        params = message.get("params", {})
        msg_id = message.get("id")
        
        if method == "initialize":
            result = await mcp_server.handle_initialize(params)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": result.model_dump()
            }
        
        elif method == "notifications/initialized":
            # No response needed for notification
            return None
        
        elif method == "tools/list":
            result = await mcp_server.handle_list_tools(params)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": result}
            }
        
        elif method == "tools/call":
            result = await mcp_server.handle_call_tool(params)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": result
            }
        
        else:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": f"Method not found: {method}"
                }
            }
    
    except Exception as e:
        logger.error(f"Error handling MCP message: {str(e)}")
        return {
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "error": {
                "code": -32603,
                "message": f"Internal error: {str(e)}"
            }
        }

async def sse_generator(request: Request):
    """Generate Server-Sent Events for MCP communication"""
    
    # Send initial connection event
    yield f"event: message\n"
    yield f"data: {json.dumps({'type': 'connection', 'status': 'connected'})}\n\n"
    
    try:
        # Read request body for MCP messages
        body = await request.body()
        if body:
            try:
                messages = json.loads(body.decode())
                if not isinstance(messages, list):
                    messages = [messages]
                
                for message in messages:
                    response = await handle_mcp_message(message)
                    if response:  # Some notifications don't need responses
                        yield f"event: message\n"
                        yield f"data: {json.dumps(response)}\n\n"
            
            except json.JSONDecodeError:
                logger.error("Invalid JSON in request body")
                error_response = {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32700,
                        "message": "Parse error"
                    }
                }
                yield f"event: message\n"
                yield f"data: {json.dumps(error_response)}\n\n"
    
    except Exception as e:
        logger.error(f"SSE error: {str(e)}")
        error_response = {
            "jsonrpc": "2.0",
            "error": {
                "code": -32603,
                "message": f"Internal error: {str(e)}"
            }
        }
        yield f"event: message\n"
        yield f"data: {json.dumps(error_response)}\n\n"

@app.post("/sse")
@app.get("/sse")
async def sse_endpoint(request: Request):
    """Server-Sent Events endpoint for MCP communication"""
    
    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "*"
    }
    
    return StreamingResponse(
        sse_generator(request),
        media_type="text/event-stream",
        headers=headers
    )

@app.options("/sse")
async def sse_options():
    """Handle CORS preflight for SSE endpoint"""
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "*"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "openfda_mcp_server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
