import os
import sys
import uuid
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from google.adk import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types
from agents.root_agent import root_agent

def main():
    print("🔭 StarForge Demo — Target: TRAPPIST-1e\n")
    
    # Check for API keys
    if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
        print("❌ Error: GOOGLE_API_KEY or GEMINI_API_KEY is not set in the environment.")
        print("Please copy .env.example to .env and fill in your Gemini API key from AI Studio.")
        sys.exit(1)
        
    storage_dir = os.path.expanduser("~/.starforge")
    os.makedirs(storage_dir, exist_ok=True)
    
    # Initialize session service
    db_path = os.path.join(storage_dir, "sessions.db")
    session_service = SqliteSessionService(db_path=db_path)
    
    # Generate unique session ID
    session_id = f"demo_trappist1_{uuid.uuid4().hex[:8]}"
    print(f"Initializing ADK Runner for session: {session_id}...")
    
    runner = Runner(
        agent=root_agent,
        session_service=session_service,
        app_name="StarForge",
        auto_create_session=True
    )
    
    query = "Create a comprehensive research brief on the TRAPPIST-1e exoplanet."
    new_message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=query)]
    )
    
    print(f"Running multi-agent pipeline with query: '{query}'...")
    print("This runs: Query Agent ➔ Analysis Agent ➔ Literature Scout ➔ Report Generator\n")
    
    events = []
    try:
        for event in runner.run(user_id="demo_user", session_id=session_id, new_message=new_message):
            # Print state changes or output events
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        print(f"[{event.author}]: {part.text[:80].strip()}...")
            events.append(event)
    except Exception as e:
        print(f"\n❌ Pipeline failed: {e}")
        sys.exit(1)
        
    # Extract final text output
    final_output = ""
    for event in events:
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    final_output += part.text
                    
    if final_output:
        # Save output to sample reports
        report_dir = os.path.join(BASE_DIR, "demo", "sample_reports")
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(report_dir, "trappist1e_brief.md")
        
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(final_output)
            
        print(f"\n🎉 Success! Research brief generated and saved to: {report_path}")
    else:
        print("\n⚠️ Warning: No final brief was output by the Report Generator Agent.")

if __name__ == "__main__":
    main()
