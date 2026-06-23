import os
import re
import sys
import json
import uuid
import base64
import logging
import tempfile
from io import BytesIO

import gradio as gr
import requests
from dotenv import load_dotenv
from PIL import Image

# Load .env file
load_dotenv()

# Add project root to path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from google.adk import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types
from agents.root_agent import root_agent
from memory.research_memory import ResearchMemoryManager

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("starforge_ui")

# Initialize directories
STORAGE_DIR = os.path.expanduser("~/.starforge")
os.makedirs(STORAGE_DIR, exist_ok=True)

# Initialize persistence layers
session_db_path = os.path.join(STORAGE_DIR, "sessions.db")
session_service = SqliteSessionService(db_path=session_db_path)
memory_manager = ResearchMemoryManager(storage_dir=os.path.join(STORAGE_DIR, "memory"))

# ---------------------------------------------------------------------------
# Robust image extraction — multiple search strategies
# ---------------------------------------------------------------------------
# Pattern: base64 data URIs (e.g. data:image/gif;base64,R0lGOD...)
_DATA_URI_RE = re.compile(r'data:image/\w+;base64,[A-Za-z0-9+/=]+')

# Pattern: **Image URL:** followed by an HTTP URL
_IMAGE_URL_LABEL_RE = re.compile(r'\*\*Image\s*URL:\*\*\s*(https?://\S+)')

# Pattern: SkyView temporary image URLs
_SKYVIEW_URL_RE = re.compile(r'https?://skyview\.gsfc\.nasa\.gov[^\s"\')\]>]+')

# Pattern: Markdown image syntax ![alt](url)
_MD_IMAGE_RE = re.compile(r'!\[.*?\]\((https?://[^\s\)]+)\)')

# Pattern: Any .gif / .png / .jpg / .jpeg image URL
_GENERIC_IMG_URL_RE = re.compile(r'https?://\S+\.(?:gif|png|jpe?g)', re.IGNORECASE)

# Pattern: MAST (Mikulski Archive) preview image download URLs
_MAST_URL_RE = re.compile(r'https?://mast\.stsci\.edu/api/v\d+\.\d+/Download/file[^\s"\')\]>]+')


def _resolve_to_pil(source: str):
    """Convert a data URI string or HTTP URL to a PIL Image, or None on failure."""
    if source.startswith("data:image/"):
        try:
            _, b64_data = source.split(",", 1)
            return Image.open(BytesIO(base64.b64decode(b64_data)))
        except Exception as exc:
            logger.error(f"[Image] Failed to decode data URI: {exc}")
            return None

    if source.startswith("http"):
        try:
            resp = requests.get(source, timeout=20)
            resp.raise_for_status()
            return Image.open(BytesIO(resp.content))
        except Exception as exc:
            logger.error(f"[Image] Failed to download {source[:120]}: {exc}")
            return None

    return None


def _extract_image_from_events(events, final_output: str):
    """Search all event data for an image and return a PIL Image or None."""

    # Gather every piece of searchable text from the run
    text_chunks = [final_output]
    func_resp_chunks = []

    for event in events:
        if not (event.content and event.content.parts):
            continue
        for part in event.content.parts:
            if part.text:
                text_chunks.append(part.text)
            if hasattr(part, "function_response") and part.function_response:
                resp = part.function_response.response
                if isinstance(resp, dict):
                    func_resp_chunks.append(json.dumps(resp, default=str))
                elif isinstance(resp, str):
                    func_resp_chunks.append(resp)
                else:
                    func_resp_chunks.append(str(resp))

    # Build one big searchable blob (function responses are highest priority)
    all_func_text = "\n".join(func_resp_chunks)
    all_output_text = "\n".join(text_chunks)
    combined = all_func_text + "\n" + all_output_text

    logger.info(f"[Image] Searching {len(combined)} chars across {len(text_chunks)} text parts, {len(func_resp_chunks)} func responses")

    # Strategy 1 — base64 data URI (the actual image bytes, most reliable)
    m = _DATA_URI_RE.search(combined)
    if m:
        logger.info(f"[Image] ✓ Found data URI ({len(m.group(0))} chars)")
        return _resolve_to_pil(m.group(0))

    # Strategy 1.5 — MAST URL (high-resolution targeted space observatory image)
    m = _MAST_URL_RE.search(combined)
    if m:
        url = m.group(0).rstrip(")")
        logger.info(f"[Image] ✓ Found MAST High-Res URL: {url[:120]}")
        return _resolve_to_pil(url)

    # Strategy 2 — **Image URL:** label pattern
    m = _IMAGE_URL_LABEL_RE.search(combined)
    if m:
        url = m.group(1).rstrip(")")
        logger.info(f"[Image] ✓ Found Image URL label: {url[:120]}")
        return _resolve_to_pil(url)

    # Strategy 3 — SkyView domain URL
    m = _SKYVIEW_URL_RE.search(combined)
    if m:
        url = m.group(0)
        logger.info(f"[Image] ✓ Found SkyView URL: {url[:120]}")
        return _resolve_to_pil(url)

    # Strategy 4 — Markdown image syntax
    m = _MD_IMAGE_RE.search(combined)
    if m:
        url = m.group(1)
        logger.info(f"[Image] ✓ Found markdown image: {url[:120]}")
        return _resolve_to_pil(url)

    # Strategy 5 — Generic image file URL
    m = _GENERIC_IMG_URL_RE.search(combined)
    if m:
        url = m.group(0)
        logger.info(f"[Image] ✓ Found generic image URL: {url[:120]}")
        return _resolve_to_pil(url)

    logger.info("[Image] ✗ No image found in any event data")
    return None


# ---------------------------------------------------------------------------
# HTML formatters for sidebar panels
# ---------------------------------------------------------------------------

def format_watchlist_html(watchlist):
    if not watchlist:
        return "<p style='color: #888; text-align: center;'>No systems in watchlist yet.</p>"
    
    html = "<div style='display: flex; flex-direction: column; gap: 10px; max-height: 400px; overflow-y: auto;'>"
    for item in watchlist:
        notes_str = f"<div style='font-size: 0.85em; color: #aaa; margin-top: 4px;'>{item['notes']}</div>" if item['notes'] else ""
        html += f"""
        <div style='background: rgba(255, 255, 255, 0.05); padding: 10px; border-radius: 6px; border-left: 3px solid #9c27b0;'>
            <div style='display: flex; justify-content: space-between; align-items: center;'>
                <strong style='color: #e040fb; font-size: 1.1em;'>{item['target_name']}</strong>
                <span style='font-size: 0.75em; color: #777;'>{item['timestamp'][:10]}</span>
            </div>
            {notes_str}
        </div>
        """
    html += "</div>"
    return html

def format_history_html(history):
    if not history:
        return "<p style='color: #888; text-align: center;'>No research history yet.</p>"
    
    html = "<div style='display: flex; flex-direction: column; gap: 8px; max-height: 400px; overflow-y: auto;'>"
    for item in history:
        target_disp = item['target_name'] or item['query']
        html += f"""
        <div style='background: rgba(255, 255, 255, 0.03); padding: 8px; border-radius: 6px; border-left: 2px solid #3f51b5;'>
            <div style='font-weight: bold; color: #64b5f6; font-size: 0.95em;'>{target_disp}</div>
            <div style='font-size: 0.8em; color: #777; margin-top: 2px;'>{item['timestamp'][:10]} | "{item['query'][:40]}..."</div>
        </div>
        """
    html += "</div>"
    return html

# ---------------------------------------------------------------------------
# Core research function
# ---------------------------------------------------------------------------

def run_research(query, session_id):
    if not query.strip():
        return "Please enter a search query.", None, gr.update(), gr.update(), ""
        
    # Check if API key exists in environment
    if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
        return (
            "⚠️ **API Key Missing**\n\nPlease configure your `GEMINI_API_KEY` in the `.env` file or environment to run the Gemini agent.",
            None,
            gr.update(),
            gr.update(),
            ""
        )
        
    # Set default user ID and generate session ID if needed
    user_id = "default_user"
    if not session_id:
        session_id = str(uuid.uuid4())
        
    new_message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=query)]
    )
    
    # Initialize Runner
    runner = Runner(
        agent=root_agent,
        session_service=session_service,
        app_name="StarForge",
        auto_create_session=True
    )
    
    # Run the pipeline
    events = []
    try:
        for event in runner.run(user_id=user_id, session_id=session_id, new_message=new_message):
            events.append(event)
    except Exception as e:
        return f"An error occurred during agent execution: {str(e)}", None, gr.update(value=session_id), gr.update(), ""
        
    # Extract final text output from ALL events
    final_output = ""
    for event in events:
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    final_output += part.text

    # ── Image extraction (robust multi-strategy) ──
    image_obj = _extract_image_from_events(events, final_output)
                    
    # Attempt to extract target exoplanet name (e.g. from Title or Query)
    target_name = None
    title_match = re.search(r'#\s*(.*?)\s*Research Brief', final_output, re.IGNORECASE)
    if title_match:
        target_name = title_match.group(1).strip()
    else:
        # Fallback to query word matching
        words = query.split()
        if words:
            target_name = words[-1]
            
    # Save to history
    memory_manager.add_to_history(query=query, brief=final_output, target_name=target_name)
    
    # Refresh lists
    history_html = format_history_html(memory_manager.get_history())
    
    return final_output, image_obj, gr.update(value=session_id), history_html, target_name or ""

def add_watchlist(target_name, notes, current_brief):
    if not target_name.strip():
        return "Please enter a target name.", gr.update()
        
    memory_manager.add_to_watchlist(target_name=target_name, notes=notes)
    watchlist_html = format_watchlist_html(memory_manager.get_watchlist())
    return f"Successfully added '{target_name}' to watchlist!", watchlist_html

def load_watchlist():
    return format_watchlist_html(memory_manager.get_watchlist())

def load_history():
    return format_history_html(memory_manager.get_history())

def save_preferences(detail_level, default_survey, skyview_color_lut):
    memory_manager.update_preferences(detail_level=detail_level, default_survey=default_survey, skyview_color_lut=skyview_color_lut)
    return "Preferences saved successfully!"

# Define Custom CSS for a stunning dark galactic aesthetic
theme_css = """
body {
    background-color: #0c0f1d !important;
}
.gradio-container {
    background: radial-gradient(circle at top, #161233 0%, #0c0f1d 100%) !important;
    font-family: 'Outfit', 'Inter', sans-serif !important;
}
.sidebar-panel {
    background: rgba(255, 255, 255, 0.03) !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    border-radius: 12px !important;
}
.title-header {
    text-align: center;
    background: linear-gradient(135deg, #e040fb, #00e5ff) !important;
    -webkit-background-clip: text !important;
    -webkit-text-fill-color: transparent !important;
    font-weight: 900 !important;
    margin-bottom: 20px !important;
}
.search-btn {
    background: linear-gradient(135deg, #7b1fa2, #00b0ff) !important;
    color: white !important;
    border: none !important;
    font-weight: bold !important;
}
.search-btn:hover {
    filter: brightness(1.2) !important;
    box-shadow: 0 0 15px rgba(0, 176, 255, 0.4) !important;
}
"""

with gr.Blocks(title="StarForge — Exoplanet Research Assistant") as app:
    session_state = gr.State("")
    
    # Title Header
    gr.HTML("<h1 class='title-header' style='font-size: 3em; margin-top: 20px; font-weight: 800;'>🔭 STARFORGE</h1>")
    gr.HTML("<p style='text-align: center; color: #8a8fb5; font-size: 1.2em; margin-bottom: 30px;'>AI-powered Multi-Agent Exoplanet Research Assistant</p>")
    
    with gr.Row():
        # Sidebar for persistence layers
        with gr.Column(scale=1, min_width=300):
            with gr.Group():
                gr.HTML("<h3 style='color: #00e5ff; margin-bottom: 10px; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 5px;'>💾 Research History</h3>")
                history_panel = gr.HTML(format_history_html(memory_manager.get_history()))
                btn_refresh_history = gr.Button("🔄 Refresh History", size="sm")
                
            gr.HTML("<div style='height: 10px;'></div>")
            
            with gr.Group():
                gr.HTML("<h3 style='color: #e040fb; margin-bottom: 10px; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 5px;'>⭐ Watchlist</h3>")
                watchlist_panel = gr.HTML(format_watchlist_html(memory_manager.get_watchlist()))
                btn_refresh_watchlist = gr.Button("🔄 Refresh Watchlist", size="sm")
                
        # Main panel
        with gr.Column(scale=3):
            with gr.Tabs():
                # Tab 1: Exoplanet Discovery Assistant
                with gr.TabItem("🔍 System Scout"):
                    with gr.Row():
                        input_query = gr.Textbox(
                            placeholder="Enter system name (e.g. 'TRAPPIST-1e', 'Kepler-22b', 'GJ 1214 b')...",
                            label="Target System Query",
                            scale=4
                        )
                        btn_submit = gr.Button("Analyze System", variant="primary", elem_classes="search-btn", scale=1)
                        
                    gr.Examples(
                        examples=[
                            "Tell me everything about TRAPPIST-1e",
                            "Retrieve parameters for Kepler-22b",
                            "Analyze GJ 1214 b",
                            "List potentially habitable zone exoplanets",
                            "What are the latest papers on exoplanet atmospheres?"
                        ],
                        inputs=input_query,
                        label="💡 Pre-set Suggestions & Examples"
                    )
                        
                    with gr.Row():
                        # Left side for markdown report
                        with gr.Column(scale=2):
                            output_report = gr.Markdown(
                                label="Exoplanet Research Brief",
                                value="Enter a query to generate a complete exoplanet profile."
                            )
                        # Right side for SkyView images
                        with gr.Column(scale=1):
                            output_image = gr.Image(
                                label="Target Sky Image (SkyView)",
                                interactive=False
                            )
                            
                            with gr.Group():
                                gr.HTML("<h4 style='color: #9c27b0; margin-top: 10px;'>Add target to watchlist</h4>")
                                input_watch_target = gr.Textbox(label="Target Name", placeholder="e.g. TRAPPIST-1e")
                                input_watch_notes = gr.Textbox(label="Notes", placeholder="e.g. Rocky planet in habitable zone")
                                btn_add_watch = gr.Button("Save to Watchlist")
                                output_watch_status = gr.Label(label="Status", value="")
                                
                # Tab 2: Search Preferences
                with gr.TabItem("⚙️ Preferences"):
                    gr.HTML("<h3 style='color: #00e5ff; margin-bottom: 10px;'>User Settings</h3>")
                    prefs = memory_manager.get_preferences()
                    input_detail = gr.Radio(
                        choices=["comprehensive", "compact", "stellar-only"],
                        value=prefs.get("detail_level", "comprehensive"),
                        label="Report Detail Level"
                    )
                    input_survey = gr.Dropdown(
                        choices=["JWST/HST (MAST)", "DSS", "DSS2 Red", "2MASS-J", "WISE 3.4", "GALEX Near UV", "RASS"],
                        value=prefs.get("default_survey", "JWST/HST (MAST)"),
                        label="Default SkyView Survey"
                    )
                    input_color_lut = gr.Dropdown(
                        choices=["gray", "Fire", "Ice", "Spectrum"],
                        value=prefs.get("skyview_color_lut", "gray"),
                        label="SkyView Color Palette (Colormap)"
                    )
                    btn_save_prefs = gr.Button("Save Preferences")
                    output_prefs_status = gr.Label(label="Preferences Status", value="")

    # Event handlers
    btn_submit.click(
        fn=run_research,
        inputs=[input_query, session_state],
        outputs=[output_report, output_image, session_state, history_panel, input_watch_target]
    )
    
    input_query.submit(
        fn=run_research,
        inputs=[input_query, session_state],
        outputs=[output_report, output_image, session_state, history_panel, input_watch_target]
    )
    
    btn_add_watch.click(
        fn=add_watchlist,
        inputs=[input_watch_target, input_watch_notes, output_report],
        outputs=[output_watch_status, watchlist_panel]
    )
    
    btn_refresh_watchlist.click(
        fn=load_watchlist,
        inputs=[],
        outputs=[watchlist_panel]
    )
    
    btn_refresh_history.click(
        fn=load_history,
        inputs=[],
        outputs=[history_panel]
    )
    
    btn_save_prefs.click(
        fn=save_preferences,
        inputs=[input_detail, input_survey, input_color_lut],
        outputs=[output_prefs_status]
    )

if __name__ == "__main__":
    app.launch(css=theme_css)
