import asyncio
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.genai import types
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from agents.root_agent import root_agent

class MockModels:
    def __init__(self):
        self.call_count = 0

    async def generate_content(self, model, contents, config, **kwargs):
        self.call_count += 1
        
        # Check system instruction in config
        system_instruction = ""
        if config and hasattr(config, "system_instruction") and config.system_instruction:
            system_instruction = config.system_instruction

        # Inspect contents to find the last message and part
        last_part = None
        if contents:
            last_content = contents[-1]
            if last_content.parts:
                last_part = last_content.parts[-1]

        # 1. Target Query Agent
        if "Target Query Agent for StarForge" in system_instruction:
            if last_part and getattr(last_part, "function_response", None):
                tool_name = last_part.function_response.name
                if tool_name == "search_planets":
                    return types.GenerateContentResponse(
                        candidates=[types.Candidate(content=types.Content(
                            role="model",
                            parts=[
                                types.Part(function_call=types.FunctionCall(
                                    id="call_get_planet_parameters",
                                    name="get_planet_parameters",
                                    args={"planet_name": "TRAPPIST-1 e"}
                                ))
                            ]
                        ))]
                    )
                elif tool_name == "get_planet_parameters":
                    return types.GenerateContentResponse(
                        candidates=[types.Candidate(content=types.Content(
                            role="model",
                            parts=[
                                types.Part(function_call=types.FunctionCall(
                                    id="call_get_stellar_parameters",
                                    name="get_stellar_parameters",
                                    args={"star_name": "TRAPPIST-1"}
                                ))
                            ]
                        ))]
                    )
                elif tool_name == "get_stellar_parameters":
                    return types.GenerateContentResponse(
                        candidates=[types.Candidate(content=types.Content(
                            role="model",
                            parts=[
                                types.Part(function_call=types.FunctionCall(
                                    id="call_get_observation_summary",
                                    name="get_observation_summary",
                                    args={"target": "TRAPPIST-1"}
                                ))
                            ]
                        ))]
                    )
                elif tool_name == "get_observation_summary":
                    return types.GenerateContentResponse(
                        candidates=[types.Candidate(content=types.Content(
                            role="model",
                            parts=[
                                types.Part.from_text(text="[Query Agent Output] Found planet TRAPPIST-1 e and star TRAPPIST-1.")
                            ]
                        ))]
                    )
            # Default first call
            return types.GenerateContentResponse(
                candidates=[types.Candidate(content=types.Content(
                    role="model",
                    parts=[
                        types.Part(function_call=types.FunctionCall(
                            id="call_search_planets",
                            name="search_planets",
                            args={"query": "TRAPPIST-1"}
                        ))
                    ]
                ))]
            )

        # 2. Data Analysis Agent
        elif "Data Analysis Agent for StarForge" in system_instruction:
            if last_part and getattr(last_part, "function_response", None):
                tool_name = last_part.function_response.name
                if tool_name == "get_sky_image":
                    return types.GenerateContentResponse(
                        candidates=[types.Candidate(content=types.Content(
                            role="model",
                            parts=[
                                types.Part.from_text(text="[Analysis Agent Output] Image of TRAPPIST-1 system retrieved. Rocky planet with possible water.")
                            ]
                        ))]
                    )
            return types.GenerateContentResponse(
                candidates=[types.Candidate(content=types.Content(
                    role="model",
                    parts=[
                        types.Part(function_call=types.FunctionCall(
                            id="call_get_sky_image",
                            name="get_sky_image",
                            args={"target": "TRAPPIST-1", "survey": "DSS"}
                        ))
                    ]
                ))]
            )

        # 3. Literature Scout Agent
        elif "Literature Scout Agent for StarForge" in system_instruction:
            if last_part and getattr(last_part, "function_response", None):
                tool_name = last_part.function_response.name
                if tool_name == "search_papers":
                    return types.GenerateContentResponse(
                        candidates=[types.Candidate(content=types.Content(
                            role="model",
                            parts=[
                                types.Part.from_text(text="[Literature Agent Output] Summarized 3 papers on TRAPPIST-1.")
                            ]
                        ))]
                    )
            return types.GenerateContentResponse(
                candidates=[types.Candidate(content=types.Content(
                    role="model",
                    parts=[
                        types.Part(function_call=types.FunctionCall(
                            id="call_search_papers",
                            name="search_papers",
                            args={"query": "TRAPPIST-1 atmosphere"}
                        ))
                    ]
                ))]
            )

        # 4. Report Generator Agent
        elif "Report Generator Agent for StarForge" in system_instruction:
            return types.GenerateContentResponse(
                candidates=[types.Candidate(content=types.Content(
                    role="model",
                    parts=[
                        types.Part.from_text(text="# TRAPPIST-1 e Research Brief\n\n## Parameters\nRocky planet.\n\n## Analysis\nPossible water.\n\n## Literature\n3 papers.")
                    ]
                ))]
            )

        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(
                role="model",
                parts=[
                    types.Part.from_text(text="Fallback")
                ]
            ))]
        )


@pytest.mark.asyncio
async def test_agent_workflow_integration():
    # Set dummy API key to pass SDK checks
    os.environ["GEMINI_API_KEY"] = "dummy-api-key"
    
    mock_models = MockModels()
    
    # Patch Client.aio.models.generate_content
    with patch("google.genai.Client") as mock_client_class:
        # Mock client instance and its nested aio.models methods
        mock_client = MagicMock()
        mock_client.vertexai = False
        mock_client.aio.models.generate_content = mock_models.generate_content
        mock_client_class.return_value = mock_client
        
        session_service = InMemorySessionService()
        runner = Runner(
            agent=root_agent,
            session_service=session_service,
            app_name="StarForge",
            auto_create_session=True
        )
        
        new_message = types.Content(
            role="user",
            parts=[types.Part.from_text(text="Generate a research brief on TRAPPIST-1e")]
        )
        
        events = []
        # Runner.run runs synchronously inside a thread pool, but yields events
        for event in runner.run(user_id="test_user", session_id="test_session", new_message=new_message):
            events.append(event)
            
        # Assertions
        assert len(events) > 0
        
        # Verify that output contains our mocked final report content
        final_output = ""
        for event in events:
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        final_output += part.text
                            
        assert "TRAPPIST-1 e Research Brief" in final_output
        assert "Rocky planet" in final_output
        assert "Possible water" in final_output
        
        # Verify the mock was called multiple times across all sub-agents
        assert mock_models.call_count >= 4
        print("✅ End-to-end integration test passed successfully!")

if __name__ == "__main__":
    asyncio.run(test_agent_workflow_integration())
