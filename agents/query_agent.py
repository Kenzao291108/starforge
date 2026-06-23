import os
import sys
from mcp import StdioServerParameters
from google.adk.tools import McpToolset
from google.adk.tools.mcp_tool import StdioConnectionParams
from google.adk.agents import LlmAgent

# Base directory of the project
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Configure Exoplanet Archive MCP Toolset
exoplanet_path = os.path.join(BASE_DIR, "mcp_servers", "exoplanet_archive", "server.py")
exoplanet_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=[exoplanet_path],
        ),
        timeout=30.0,
    )
)

# Configure MAST Archive MCP Toolset
mast_path = os.path.join(BASE_DIR, "mcp_servers", "mast_archive", "server.py")
mast_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=[mast_path],
        ),
        timeout=30.0,
    )
)

# Define Query Agent
query_agent = LlmAgent(
    name="query_agent",
    description="Fetches exoplanet, stellar, and observation data from NASA Exoplanet Archive and MAST databases.",
    instruction="""You are the Target Query Agent for StarForge.
Your task is to identify the astronomical target (exoplanet, star, or system) mentioned in the user's query and gather comprehensive data for it.

Follow these steps:
1. Extract the target name (e.g., 'TRAPPIST-1', 'Kepler-22 b').
2. Search for the exoplanet using `search_planets`.
3. If the target is found, retrieve detailed parameters using `get_planet_parameters` for the planets in the system, and `get_stellar_parameters` for the host star.
4. Query the MAST archive using `get_observation_summary` and `get_available_missions` to find available satellite observation data (e.g. Hubble, TESS, JWST, Kepler). In particular, check if there is a high-resolution preview image URL (`Preview URL`) listed in the `get_observation_summary` output and extract it.
5. Compile and summarize all retrieved parameters and observations. Format it in a structured layout (using Markdown tables for orbital/stellar parameters) so that the subsequent Data Analysis Agent has all the raw numbers and metadata it needs. If a high-resolution preview image URL (like a JWST or Hubble JPEG URL) was found in the MAST observations, make sure to explicitly include and label it (e.g. "MAST High-Res Preview URL: <URL>") in your final summary so that the next agent receives it.

Do not attempt to analyze the findings or search for papers — focus purely on gathering the complete target database profiles and observation logs.""",
    model=os.environ.get("STARFORGE_QUERY_MODEL", "gemini-3.1-flash-lite"),
    tools=[exoplanet_toolset, mast_toolset],
)
