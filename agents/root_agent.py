from google.adk.workflow import Workflow, Edge, START
from agents.query_agent import query_agent
from agents.analysis_agent import analysis_agent
from agents.literature_agent import literature_agent
from agents.report_agent import report_agent

# Define Root Agent as a Workflow instead of deprecated SequentialAgent
root_agent = Workflow(
    name="root_agent",
    description="StarForge root agent that orchestrates the exoplanet research pipeline (Query -> Analysis -> Literature -> Report).",
    edges=[
        Edge(from_node=START, to_node=query_agent),
        Edge(from_node=query_agent, to_node=analysis_agent),
        Edge(from_node=analysis_agent, to_node=literature_agent),
        Edge(from_node=literature_agent, to_node=report_agent),
    ],
)
