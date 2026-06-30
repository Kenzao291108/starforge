import os
import sys
from mcp import StdioServerParameters
from google.adk.tools import McpToolset
from google.adk.tools.mcp_tool import StdioConnectionParams
from google.adk.agents import LlmAgent

# Base directory of the project
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Configure arXiv Astro MCP Toolset
arxiv_path = os.path.join(BASE_DIR, "mcp_servers", "arxiv_astro", "server.py")
arxiv_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=[arxiv_path],
        ),
        timeout=60.0,
    )
)

# Define Literature Agent
literature_agent = LlmAgent(
    name="literature_agent",
    description="Finds and summarizes scientific literature on exoplanets and stellar systems from arXiv.",
    instruction="""You are the Literature Scout Agent for StarForge.
Your task is to find and summarize the latest scientific papers about the target system from the arXiv database.

Follow these steps:
1. Identify the target system name and key scientific topics of interest (e.g., 'TRAPPIST-1 atmosphere', 'Kepler-22 b habitability', or host star activity).
2. Search for scientific literature on arXiv using the `search_papers` tool (and `search_recent_papers` if looking for recent publications). Target the `astro-ph.EP` (Earth and Planetary Astrophysics) category.
3. Select the 3 to 5 most relevant papers. For each selected paper, compile:
   - Paper Title and Authors.
   - Publication Date.
   - arXiv ID and URL link.
   - A brief summary of key findings (e.g., atmospheric detection, water signatures, orbit refinement) and their significance.
4. If a paper is highly critical, you can query its full abstract using `get_paper_abstract` or find similar papers using `find_related_papers` to provide deeper context.
5. Compile these paper summaries into a structured literature review section to pass to the Report Generator Agent.

Focus entirely on academic research, observational papers, and theoretical studies. Do not try to structure the final exoplanet research brief.""",
    model=os.environ.get("STARFORGE_LITERATURE_MODEL", "gemini-3.1-flash-lite"),
    tools=[arxiv_toolset],
)
