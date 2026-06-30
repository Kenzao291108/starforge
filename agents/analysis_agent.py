import os
import sys
from mcp import StdioServerParameters
from google.adk.tools import McpToolset
from google.adk.tools.mcp_tool import StdioConnectionParams
from google.adk.agents import LlmAgent

# Base directory of the project
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Configure SkyView MCP Toolset
skyview_path = os.path.join(BASE_DIR, "mcp_servers", "skyview", "server.py")
skyview_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=[skyview_path],
        ),
        timeout=60.0,
    )
)

# Define Analysis Agent
analysis_agent = LlmAgent(
    name="analysis_agent",
    description="Analyzes the physical and orbital properties of exoplanets and retrieves sky images of target systems.",
    instruction="""You are the Data Analysis Agent for StarForge.
Your task is to analyze the exoplanet system properties gathered by the Query Agent and retrieve celestial imagery of the target system.

Follow these steps:
1. Examine the parameters of the exoplanet(s) and host star provided in the conversation history.
2. Formulate a scientific analysis of the system, addressing key factors:
   - Planetary density and likely composition (e.g., rocky, gas giant, ice giant).
   - Habitability indicators (e.g., equilibrium temperature, location relative to the host star's habitable zone).
   - Surface gravity estimates.
   - Host star properties (spectral type, luminosity, temperature) and their implications for the system's planets.
3. You MUST call the `get_sky_image` tool with the target name to retrieve a sky image. The tool automatically uses the user's preferred survey from their settings. Do NOT pass a survey argument — let the tool choose.
4. Include the image URL returned by the tool in your final output using exactly this format: **Image URL:** <URL>
5. CRITICAL RULES:
   - ALWAYS call `get_sky_image`. Never skip it.
   - NEVER invent, guess, or hallucinate image URLs.
   - NEVER use Wikipedia or Wikimedia URLs.
   - ONLY use the exact URL returned by the `get_sky_image` tool.
   - Place the **Image URL:** on its own line with a blank line after it.

Focus on physical modeling, habitability context, and observational visualization. Do not search for literature or compile the final report.""",
    model=os.environ.get("STARFORGE_ANALYSIS_MODEL", "gemini-3.1-flash-lite"),
    tools=[skyview_toolset],
)
