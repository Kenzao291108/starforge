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
        timeout=30.0,
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
3. Check the conversation history first. If the Query Agent found and passed a high-resolution space observatory preview image URL (labeled as 'MAST High-Res Preview URL') from JWST or HST/Hubble, use that URL and its metadata as the target sky image and skip calling `get_sky_image`. Otherwise (if no high-resolution MAST image is in history), retrieve a sky image of the target host star using the `get_sky_image` tool without specifying a survey or size/field of view, letting the tool use the user's preferred survey and its native optimal scale.
4. Include the image metadata, source telescope (e.g. 'James Webb Space Telescope (JWST)', 'Hubble Space Telescope (HST)', or 'SkyView DSS Survey'), and URL in your report. Ensure you explicitly write the URL using the pattern **Image URL:** <URL> so that the system and subsequent agents can easily extract it.
5. Summarize your scientific findings and the sky image reference. Format this analysis clearly to pass to the Literature Scout Agent.

Focus on physical modeling, habitability context, and observational visualization. Do not search for literature or compile the final report.""",
    model=os.environ.get("STARFORGE_ANALYSIS_MODEL", "gemini-3.1-flash-lite"),
    tools=[skyview_toolset],
)
