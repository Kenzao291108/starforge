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
Image.MAX_IMAGE_PIXELS = None

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

# Pattern: **Image URL:** followed by an HTTP/file URL
_IMAGE_URL_LABEL_RE = re.compile(r'\*\*Image\s*URL:\*\*\s*((?:https?|file)://\S+)')

# Pattern: SkyView temporary image URLs
_SKYVIEW_URL_RE = re.compile(r'(?:https?|file)://skyview\.gsfc\.nasa\.gov[^\s"\')>]+')

# Pattern: Markdown image syntax ![alt](url)
_MD_IMAGE_RE = re.compile(r'!\[.*?\]\(((?:https?|file)://[^\s\)]+)\)')

# Pattern: Any .gif / .png / .jpg / .jpeg image URL
_GENERIC_IMG_URL_RE = re.compile(r'(?:https?|file)://\S+\.(?:gif|png|jpe?g)', re.IGNORECASE)

# Pattern: MAST (Mikulski Archive) preview image download URLs
_MAST_URL_RE = re.compile(r'https?://mast\.stsci.edu/api/v\d+\.\d+/Download/file[^\s"\')>]+')


def _resolve_to_pil(source: str):
    """Convert a data URI string, HTTP URL, or local file:// path to a PIL Image, or None on failure."""
    if source.startswith("data:image/"):
        try:
            _, b64_data = source.split(",", 1)
            return Image.open(BytesIO(base64.b64decode(b64_data)))
        except Exception as exc:
            logger.error(f"[Image] Failed to decode data URI: {exc}")
            return None

    if source.startswith("file://"):
        try:
            # Extract absolute path
            path = source[7:]
            if os.path.exists(path):
                return Image.open(path)
            else:
                logger.error(f"[Image] Local file not found: {path}")
                return None
        except Exception as exc:
            logger.error(f"[Image] Failed to load local file {source}: {exc}")
            return None

    if source.startswith("http"):
        try:
            import hashlib
            # Use hash of URL to determine local cache path
            cache_dir = os.path.expanduser("~/.starforge/cache")
            url_hash = hashlib.md5(source.encode("utf-8")).hexdigest()
            cached_path = os.path.join(cache_dir, f"cached_{url_hash}.gif")
            
            # Check if cached locally first to avoid network requests
            if os.path.exists(cached_path):
                try:
                    return Image.open(cached_path)
                except Exception:
                    # If cached image is corrupted, delete it and fallback to download
                    os.remove(cached_path)

            headers = {"User-Agent": "StarForge/1.0 (Research Assistant UI)"}
            resp = requests.get(source, headers=headers, timeout=20)
            resp.raise_for_status()
            
            # Save to cache
            os.makedirs(cache_dir, exist_ok=True)
            try:
                with open(cached_path, "wb") as f:
                    f.write(resp.content)
            except Exception as cache_err:
                logger.warning(f"[Image] Failed to write cache file: {cache_err}")
                
            return Image.open(BytesIO(resp.content))
        except Exception as exc:
            logger.error(f"[Image] Failed to download {source[:120]}: {exc}")
            return None

    return None


def _clean_url(url: str) -> str:
    """Aggressively clean stray characters or glued words from LLM generated URLs."""
    url = url.rstrip(")#*><[] \n\r.,!?;:'\"")
    # If the LLM glued a word like "The" directly after the .jpg (e.g. .jpgThe)
    for ext in [".jpg", ".jpeg", ".png", ".gif", ".fits"]:
        idx = url.lower().rfind(ext)
        if idx != -1:
            end_idx = idx + len(ext)
            # If there's garbage after the extension and no query parameters are there
            if end_idx < len(url) and "?" not in url[end_idx:]:
                url = url[:end_idx]

    # Reconstruct and clean SkyView query parameters if any words were glued to the end of them
    if "skyview.gsfc.nasa.gov" in url.lower() and "?" in url:
        try:
            from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
            import re
            parsed = urlparse(url)
            qsl = parse_qsl(parsed.query)
            clean_qsl = []
            for k, v in qsl:
                # Chop off any trailing text glued to numeric parameters like Size or Pixels
                if k.lower() in ("size", "pixels"):
                    num_match = re.match(r'^([0-9.]+)', v)
                    if num_match:
                        v = num_match.group(1)
                elif k.lower() == "projection":
                    # Standard projection is Tan. Cut off any trailing alphabetical characters.
                    for proj in ("tan", "car", "sin", "ait", "zea", "gnom", "orth"):
                        if v.lower().startswith(proj):
                            v = v[:len(proj)]
                            break
                elif k.lower() == "return":
                    if v.lower().startswith("gif"):
                        v = "GIF"
                    elif v.lower().startswith("fits"):
                        v = "FITS"
                clean_qsl.append((k, v))
            
            new_query = urlencode(clean_qsl)
            url = urlunparse((
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                new_query,
                parsed.fragment
            ))
        except Exception:
            pass

    return url

def _clean_report_text(text: str) -> str:
    """Find all URLs in the report text, clean them of glued trailing words, and insert spaces/newlines."""
    import re
    # Match any URL-like string starting with http://, https://, or file://
    url_pattern = re.compile(r'((?:https?|file)://\S+)')
    
    def repl(match):
        orig_url = match.group(1)
        cleaned_url = _clean_url(orig_url)
        if cleaned_url != orig_url:
            remainder = orig_url[len(cleaned_url):]
            # Separate the remainder word with a space
            return f"{cleaned_url} {remainder}"
        return orig_url

    return url_pattern.sub(repl, text)

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

    attempted_urls = set()

    # Strategy 1 — base64 data URI (the actual image bytes, most reliable)
    m = _DATA_URI_RE.search(combined)
    if m:
        logger.info(f"[Image] ✓ Found data URI ({len(m.group(0))} chars)")
        return _resolve_to_pil(m.group(0))

    # Strategy 2 — **Image URL:** label pattern
    for m in _IMAGE_URL_LABEL_RE.finditer(combined):
        url = _clean_url(m.group(1))
        if url in attempted_urls:
            continue
        if "wikipedia.org" in url.lower() or "wikimedia.org" in url.lower():
            continue
        attempted_urls.add(url)
        logger.info(f"[Image] ✓ Found Image URL label: {url[:120]}")
        res = _resolve_to_pil(url)
        if res: return res

    # Strategy 3 — SkyView domain URL
    for m in _SKYVIEW_URL_RE.finditer(combined):
        url = _clean_url(m.group(0))
        if url in attempted_urls:
            continue
        if "wikipedia.org" in url.lower() or "wikimedia.org" in url.lower():
            continue
        attempted_urls.add(url)
        logger.info(f"[Image] ✓ Found SkyView URL: {url[:120]}")
        res = _resolve_to_pil(url)
        if res: return res

    # Strategy 4 — Markdown image syntax
    for m in _MD_IMAGE_RE.finditer(combined):
        url = _clean_url(m.group(1))
        if url in attempted_urls:
            continue
        if "wikipedia.org" in url.lower() or "wikimedia.org" in url.lower():
            continue
        attempted_urls.add(url)
        logger.info(f"[Image] ✓ Found Markdown image: {url[:120]}")
        res = _resolve_to_pil(url)
        if res: return res

    # Strategy 5 — Generic image file URL
    for m in _GENERIC_IMG_URL_RE.finditer(combined):
        url = _clean_url(m.group(0))
        if url in attempted_urls:
            continue
        if "wikipedia.org" in url.lower() or "wikimedia.org" in url.lower():
            continue
        attempted_urls.add(url)
        logger.info(f"[Image] ✓ Found generic image URL: {url[:120]}")
        res = _resolve_to_pil(url)
        if res: return res

    logger.info("[Image] ✗ No image found in any event data")
    return None


# ---------------------------------------------------------------------------
# HTML formatters
# ---------------------------------------------------------------------------

def format_watchlist_html(watchlist):
    """Render watchlist as premium glassmorphism cards."""
    if not watchlist:
        return """
        <div style="text-align: center; padding: 28px 16px; color: #6b7094;">
            <div style="font-size: 2em; margin-bottom: 8px; opacity: 0.5;">⭐</div>
            <div style="font-size: 0.9em;">No systems in watchlist yet.</div>
            <div style="font-size: 0.78em; color: #4a4f6e; margin-top: 4px;">Analyze a target and add it here to track.</div>
        </div>"""

    html = "<div style='display: flex; flex-direction: column; gap: 8px;'>"
    for idx, item in enumerate(watchlist):
        notes_html = ""
        if item.get("notes"):
            notes_html = f"<div style='font-size: 0.82em; color: #9ca3c4; margin-top: 6px; line-height: 1.4;'>{item['notes'][:80]}</div>"

        html += f"""
        <div class="watchlist-card" style="
            background: linear-gradient(135deg, rgba(156, 39, 176, 0.08), rgba(0, 229, 255, 0.05));
            border: 1px solid rgba(156, 39, 176, 0.2);
            border-radius: 10px;
            padding: 12px 14px;
            transition: all 0.25s ease;
            position: relative;
        ">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span style="font-size: 1.1em;">🪐</span>
                    <span style="color: #e040fb; font-weight: 600; font-size: 0.95em;">{item['target_name']}</span>
                </div>
                <span style="
                    font-size: 0.7em;
                    color: #5a5f80;
                    background: rgba(255,255,255,0.04);
                    padding: 2px 8px;
                    border-radius: 10px;
                ">{item['timestamp'][:10]}</span>
            </div>
            {notes_html}
        </div>"""

    html += "</div>"
    return html


def format_history_html(history):
    """Render history as compact clickable items for the sidebar."""
    if not history:
        return """
        <div style="text-align: center; padding: 24px 12px; color: #5a5f80;">
            <div style="font-size: 1.5em; margin-bottom: 6px; opacity: 0.4;">🔭</div>
            <div style="font-size: 0.82em;">No research yet.</div>
            <div style="font-size: 0.75em; color: #3e4260; margin-top: 4px;">Start by analyzing a target system.</div>
        </div>"""

    html = "<div style='display: flex; flex-direction: column; gap: 4px;'>"
    for idx, item in enumerate(history):
        target_disp = item.get('target_name') or item.get('query', 'Unknown')
        query_snippet = item.get('query', '')[:45]
        date_str = item.get('timestamp', '')[:10]

        html += f"""
        <div class="history-item" style="
            padding: 10px 12px;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s ease;
            border-left: 2px solid transparent;
        " onclick="
            document.querySelector('#search-input textarea').value = '{query_snippet.replace(chr(39), "").replace(chr(10), "")}';
            document.querySelector('#search-input textarea').dispatchEvent(new Event('input', {{bubbles: true}}));
        ">
            <div style="display: flex; align-items: center; gap: 6px;">
                <span style="color: #7c8aff; font-size: 0.75em;">●</span>
                <span style="color: #c8cdf5; font-weight: 500; font-size: 0.88em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 200px;">{target_disp}</span>
            </div>
            <div style="font-size: 0.75em; color: #5a5f80; margin-top: 3px; padding-left: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{query_snippet}…</div>
            <div style="font-size: 0.68em; color: #3e4260; margin-top: 2px; padding-left: 14px;">{date_str}</div>
        </div>"""

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

    # Run the pipeline with automatic retries for transient API errors (503/429)
    events = []
    max_retries = 3
    retry_delay = 2.0  # seconds
    for attempt in range(max_retries + 1):
        try:
            events = []
            for event in runner.run(user_id=user_id, session_id=session_id, new_message=new_message):
                events.append(event)
            break  # Success, exit retry loop
        except Exception as e:
            err_msg = str(e)
            is_transient = any(
                err in err_msg.lower()
                for err in (
                    "503", "unavailable", "429", "resource_exhausted", "limit",
                    "connect", "connection", "timeout", "name resolution", "dns"
                )
            )
            if is_transient and attempt < max_retries:
                import time
                logger.warning(
                    f"Transient or network error (attempt {attempt + 1}/{max_retries + 1}): {err_msg}. "
                    f"Retrying in {retry_delay}s..."
                )
                time.sleep(retry_delay)
                retry_delay *= 2.0  # Exponential backoff
            else:
                logger.error(f"Agent execution failed after {attempt + 1} attempts: {err_msg}")
                return f"An error occurred during agent execution: {err_msg}", None, gr.update(value=session_id), gr.update(), ""

    # Extract final text output from ALL events
    final_output = ""
    for event in events:
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    final_output += part.text

    # Clean report text of any query-glued words
    final_output = _clean_report_text(final_output)

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
    return f"✓ Added '{target_name}'", watchlist_html


def remove_watchlist(target_name):
    if not target_name.strip():
        return "Enter a target name to remove.", gr.update()
    removed = memory_manager.remove_from_watchlist(target_name)
    watchlist_html = format_watchlist_html(memory_manager.get_watchlist())
    if removed:
        return f"✓ Removed '{target_name}'", watchlist_html
    return f"'{target_name}' not found in watchlist.", watchlist_html


def load_watchlist():
    return format_watchlist_html(memory_manager.get_watchlist())


def load_history():
    return format_history_html(memory_manager.get_history())


def save_preferences(detail_level, default_survey, skyview_color_lut, skyview_fov_arcmin):
    memory_manager.update_preferences(
        detail_level=detail_level,
        default_survey=default_survey,
        skyview_color_lut=skyview_color_lut,
        skyview_fov_arcmin=skyview_fov_arcmin
    )
    return "✓ Preferences saved"


# ---------------------------------------------------------------------------
# Premium CSS Theme
# ---------------------------------------------------------------------------
theme_css = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800;900&display=swap');

/* ── Global ── */
body {
    background-color: #08091a !important;
}

.gradio-container {
    background: radial-gradient(ellipse at 20% 0%, #161233 0%, #0c0f1d 40%, #08091a 100%) !important;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
    max-width: 100% !important;
}

/* ── Sidebar ── */
.sidebar {
    background: linear-gradient(180deg, #0e1029 0%, #0a0c1e 100%) !important;
    border-right: 1px solid rgba(124, 138, 255, 0.1) !important;
    font-family: 'Inter', sans-serif !important;
}

.sidebar .label-wrap {
    background: transparent !important;
}

.sidebar-brand {
    text-align: center;
    padding: 8px 16px 16px;
    border-bottom: 1px solid rgba(124, 138, 255, 0.08);
    margin-bottom: 12px;
}

.sidebar-brand h2 {
    background: linear-gradient(135deg, #e040fb 0%, #7c8aff 50%, #00e5ff 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-family: 'Outfit', sans-serif;
    font-weight: 800;
    font-size: 1.4em;
    margin: 0;
    letter-spacing: -0.02em;
}

.sidebar-section-label {
    font-size: 0.68em;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: #4a4f6e;
    padding: 4px 16px;
    font-weight: 600;
}

/* History items hover */
.history-item:hover {
    background: rgba(124, 138, 255, 0.08) !important;
    border-left-color: #7c8aff !important;
}

/* ── Header ── */
.main-header {
    text-align: center;
    margin-bottom: 4px;
}

.main-header h1 {
    font-family: 'Outfit', sans-serif !important;
    font-weight: 900 !important;
    font-size: 2.5em !important;
    background: linear-gradient(135deg, #e040fb 0%, #7c8aff 40%, #00e5ff 100%) !important;
    -webkit-background-clip: text !important;
    -webkit-text-fill-color: transparent !important;
    background-size: 200% 200% !important;
    animation: shimmer 4s ease-in-out infinite !important;
    margin: 0 !important;
    letter-spacing: -0.03em;
}

@keyframes shimmer {
    0%, 100% { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
}

.main-subtitle {
    color: #6b7094;
    font-size: 0.92em;
    font-weight: 400;
    margin-top: 2px;
}

/* ── Settings Accordion ── */
.settings-accordion {
    border: 1px solid rgba(124, 138, 255, 0.12) !important;
    border-radius: 12px !important;
    background: rgba(14, 16, 41, 0.6) !important;
    backdrop-filter: blur(10px) !important;
    margin-bottom: 16px !important;
}

.settings-accordion .label-wrap {
    padding: 10px 16px !important;
    color: #9ca3c4 !important;
    font-size: 0.88em !important;
}

.settings-accordion .label-wrap:hover {
    color: #c8cdf5 !important;
}

/* ── Search Area ── */
.search-area {
    margin-bottom: 8px !important;
}

.search-area textarea {
    background: rgba(255, 255, 255, 0.04) !important;
    border: 1px solid rgba(124, 138, 255, 0.15) !important;
    border-radius: 12px !important;
    color: #e0e3f0 !important;
    font-size: 0.95em !important;
    padding: 14px 16px !important;
    transition: border-color 0.3s ease, box-shadow 0.3s ease !important;
}

.search-area textarea:focus {
    border-color: rgba(124, 138, 255, 0.4) !important;
    box-shadow: 0 0 20px rgba(124, 138, 255, 0.1) !important;
}

.search-btn {
    background: linear-gradient(135deg, #7b1fa2 0%, #3f51b5 50%, #00b0ff 100%) !important;
    color: white !important;
    border: none !important;
    border-radius: 12px !important;
    font-weight: 600 !important;
    font-size: 0.92em !important;
    transition: all 0.3s ease !important;
    box-shadow: 0 4px 15px rgba(63, 81, 181, 0.3) !important;
    padding: 12px 24px !important;
}

.search-btn:hover {
    filter: brightness(1.15) !important;
    box-shadow: 0 6px 25px rgba(63, 81, 181, 0.5) !important;
    transform: translateY(-1px) !important;
}

/* ── Watchlist Cards ── */
.watchlist-card:hover {
    background: linear-gradient(135deg, rgba(156, 39, 176, 0.14), rgba(0, 229, 255, 0.08)) !important;
    border-color: rgba(156, 39, 176, 0.35) !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 12px rgba(156, 39, 176, 0.15) !important;
}

/* ── Output Report ── */
.report-output {
    background: rgba(255, 255, 255, 0.02) !important;
    border: 1px solid rgba(124, 138, 255, 0.08) !important;
    border-radius: 12px !important;
    padding: 20px !important;
}

.report-output h1, .report-output h2, .report-output h3 {
    color: #c8cdf5 !important;
}

.report-output table {
    border-collapse: collapse !important;
    width: 100% !important;
}

.report-output th {
    background: rgba(124, 138, 255, 0.1) !important;
    color: #7c8aff !important;
    padding: 8px 12px !important;
}

.report-output td {
    border: 1px solid rgba(124, 138, 255, 0.08) !important;
    padding: 6px 12px !important;
    color: #b0b5d4 !important;
}

/* ── Sky Image Preview Panel ── */
.sky-image-container {
    border: 1px solid rgba(0, 229, 255, 0.15) !important;
    border-radius: 14px !important;
    overflow: hidden !important;
    background: linear-gradient(180deg, rgba(0, 229, 255, 0.03) 0%, rgba(14, 16, 41, 0.8) 100%) !important;
    backdrop-filter: blur(12px) !important;
    height: 450px !important;
    position: relative !important;
    transition: border-color 0.4s ease, box-shadow 0.4s ease !important;
}

.sky-image-container:hover {
    border-color: rgba(0, 229, 255, 0.3) !important;
    box-shadow: 0 0 30px rgba(0, 229, 255, 0.08), inset 0 0 20px rgba(0, 229, 255, 0.02) !important;
}

/* Image display — fill container, no cropping */
.sky-image-container img {
    object-fit: contain !important;
    height: 100% !important;
    width: 100% !important;
    border-radius: 0 !important;
}

/* Style the label/header */
.sky-image-container > .label-wrap,
.sky-image-container > div > .label-wrap {
    background: linear-gradient(90deg, rgba(0, 229, 255, 0.08), transparent) !important;
    border-bottom: 1px solid rgba(0, 229, 255, 0.1) !important;
    padding: 8px 14px !important;
}

.sky-image-container > .label-wrap span,
.sky-image-container > div > .label-wrap span {
    color: #00e5ff !important;
    font-family: 'Outfit', sans-serif !important;
    font-weight: 600 !important;
    font-size: 0.9em !important;
    letter-spacing: 0.02em !important;
}

/* Hide upload-related elements (drag-drop overlay, upload button, etc.) */
.sky-image-container .upload-container,
.sky-image-container [data-testid="upload-button"],
.sky-image-container .image-container .upload-text,
.sky-image-container .icon-wrap {
    display: none !important;
}

/* Empty state placeholder */
.sky-image-container .image-container {
    background: radial-gradient(ellipse at center, rgba(0, 229, 255, 0.04) 0%, transparent 70%) !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
}

/* Download & fullscreen buttons styling */
.sky-image-container button {
    background: rgba(0, 229, 255, 0.1) !important;
    border: 1px solid rgba(0, 229, 255, 0.2) !important;
    color: #00e5ff !important;
    border-radius: 6px !important;
    transition: all 0.2s ease !important;
}

.sky-image-container button:hover {
    background: rgba(0, 229, 255, 0.2) !important;
    border-color: rgba(0, 229, 255, 0.4) !important;
    box-shadow: 0 2px 8px rgba(0, 229, 255, 0.15) !important;
}

/* ── Fullscreen Fixes for Sky Image ── */
.sky-image-container:fullscreen,
.sky-image-container:-webkit-full-screen,
.sky-image-container:-moz-full-screen,
.sky-image-container:-ms-fullscreen {
    height: 100vh !important;
    max-height: 100vh !important;
    width: 100vw !important;
    max-width: 100vw !important;
    background-color: #0b0c15 !important;
    overflow: visible !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
}

.sky-image-container:fullscreen img,
.sky-image-container:-webkit-full-screen img,
.sky-image-container:-moz-full-screen img,
.sky-image-container:-ms-fullscreen img {
    height: 100vh !important;
    width: 100vw !important;
    object-fit: contain !important;
}

.sky-image-container:fullscreen .upload-container,
.sky-image-container:-webkit-full-screen .upload-container,
.sky-image-container:-moz-full-screen .upload-container,
.sky-image-container:-ms-fullscreen .upload-container {
    display: block !important;

    height: 100% !important;
    width: 100% !important;
}


/* ── Watchlist Section ── */
.watchlist-section {
    border: 1px solid rgba(156, 39, 176, 0.12) !important;
    border-radius: 12px !important;
    background: rgba(156, 39, 176, 0.03) !important;
    padding: 16px !important;
    margin-top: 12px !important;
}

.watchlist-section-title {
    color: #e040fb;
    font-family: 'Outfit', sans-serif;
    font-weight: 600;
    font-size: 1.05em;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 6px;
}

/* ── Misc overrides ── */
.gr-group {
    border-color: rgba(124, 138, 255, 0.1) !important;
    background: transparent !important;
}

.gr-button.secondary {
    background: rgba(255, 255, 255, 0.04) !important;
    border: 1px solid rgba(124, 138, 255, 0.15) !important;
    color: #9ca3c4 !important;
    border-radius: 8px !important;
    font-size: 0.82em !important;
    transition: all 0.2s ease !important;
}

.gr-button.secondary:hover {
    background: rgba(124, 138, 255, 0.08) !important;
    border-color: rgba(124, 138, 255, 0.25) !important;
    color: #c8cdf5 !important;
}

.gr-input, .gr-dropdown, .gr-radio {
    background: rgba(255, 255, 255, 0.03) !important;
    border-color: rgba(124, 138, 255, 0.12) !important;
    color: #b0b5d4 !important;
}

/* Examples styling */
.gr-examples {
    border: none !important;
    background: transparent !important;
}

.gr-sample-textbox {
    background: rgba(124, 138, 255, 0.06) !important;
    border: 1px solid rgba(124, 138, 255, 0.12) !important;
    border-radius: 8px !important;
    color: #9ca3c4 !important;
    font-size: 0.85em !important;
    transition: all 0.2s ease !important;
}

.gr-sample-textbox:hover {
    background: rgba(124, 138, 255, 0.12) !important;
    border-color: rgba(124, 138, 255, 0.25) !important;
    color: #c8cdf5 !important;
}

/* Labels */
label span {
    color: #8a8fb5 !important;
    font-weight: 500 !important;
    font-size: 0.88em !important;
}

/* Scrollbar */
::-webkit-scrollbar {
    width: 5px;
}

::-webkit-scrollbar-track {
    background: transparent;
}

::-webkit-scrollbar-thumb {
    background: rgba(124, 138, 255, 0.2);
    border-radius: 10px;
}

::-webkit-scrollbar-thumb:hover {
    background: rgba(124, 138, 255, 0.35);
}

/* ── Status toast ── */
.status-toast {
    font-size: 0.85em !important;
    color: #7c8aff !important;
}
"""

# ---------------------------------------------------------------------------
# Build the Gradio Blocks UI
# ---------------------------------------------------------------------------

with gr.Blocks(title="StarForge — Exoplanet Research Assistant") as app:
    session_state = gr.State("")

    # ── Left Sidebar: Conversation History ──
    with gr.Sidebar(label="🔭 StarForge", position="left", width=300, open=True):
        gr.HTML("""
            <div class="sidebar-brand">
                <h2>🔭 STARFORGE</h2>
                <div style="font-size: 0.75em; color: #4a4f6e; margin-top: 2px;">Exoplanet Research AI</div>
            </div>
        """)

        gr.HTML("<div class='sidebar-section-label'>Research History</div>")
        history_panel = gr.HTML(
            value=format_history_html(memory_manager.get_history()),
            elem_id="history-panel"
        )
        btn_refresh_history = gr.Button("↻ Refresh", size="sm", variant="secondary")

    # ── Main Content Area ──

    # Header
    gr.HTML("""
        <div class="main-header">
            <h1>🔭 STARFORGE</h1>
            <div class="main-subtitle">AI-powered Multi-Agent Exoplanet Research Assistant</div>
        </div>
    """)

    # Settings Accordion
    with gr.Accordion("⚙️  Settings & Preferences", open=False, elem_classes="settings-accordion"):
        prefs = memory_manager.get_preferences()
        with gr.Row():
            input_detail = gr.Radio(
                choices=["comprehensive", "compact", "stellar-only"],
                value=prefs.get("detail_level", "comprehensive"),
                label="Report Detail Level",
                scale=1
            )
            input_survey = gr.Dropdown(
                choices=["JWST/HST (MAST)", "DSS", "DSS2 Red", "2MASS-J", "WISE 3.4", "GALEX Near UV", "RASS"],
                value=prefs.get("default_survey", "JWST/HST (MAST)"),
                label="Default SkyView Survey",
                scale=1
            )
            input_color_lut = gr.Dropdown(
                choices=["Original/None", "gray", "Fire", "Ice", "Spectrum"],
                value=prefs.get("skyview_color_lut", "Original/None" if prefs.get("default_survey") == "JWST/HST (MAST)" else "gray"),
                label="Color Palette",
                scale=1
            )
            input_fov = gr.Slider(
                minimum=5,
                maximum=60,
                step=1,
                value=prefs.get("skyview_fov_arcmin", 15),
                label="Field of View (Arcminutes)",
                scale=1
            )
            
            def auto_select_palette(survey):
                if survey == "JWST/HST (MAST)":
                    return "Original/None"
                return gr.update()
            
            input_survey.change(fn=auto_select_palette, inputs=[input_survey], outputs=[input_color_lut])
        with gr.Row():
            btn_save_prefs = gr.Button("💾  Save Preferences", size="sm", variant="secondary")
            output_prefs_status = gr.HTML(value="", elem_classes="status-toast")

    # Search Bar
    with gr.Row(elem_classes="search-area"):
        input_query = gr.Textbox(
            placeholder="Search for a target system — e.g. 'TRAPPIST-1e', 'Kepler-22b', 'Proxima Centauri b'…",
            label="Target System Query",
            scale=4,
            elem_id="search-input",
            lines=1
        )
        btn_submit = gr.Button(
            "🔍  Analyze System",
            variant="primary",
            elem_classes="search-btn",
            scale=1
        )

    # Example Suggestions
    gr.Examples(
        examples=[
            "Tell me everything about TRAPPIST-1e",
            "Retrieve parameters for Kepler-22b",
            "Analyze GJ 1214 b",
            "List potentially habitable zone exoplanets",
            "What are the latest papers on exoplanet atmospheres?"
        ],
        inputs=input_query,
        label="💡 Quick Suggestions"
    )

    # ── Results: Two-Column Layout ──
    with gr.Row():
        # Left: Report
        with gr.Column(scale=2):
            output_report = gr.Markdown(
                label="Research Brief",
                value="Enter a target system above to generate a comprehensive exoplanet research profile.",
                elem_classes="report-output"
            )

        # Right: Sky Image + Watchlist
        with gr.Column(scale=1):
            output_image = gr.Image(
                label="🌌 Target Sky Image",
                interactive=False,
                height=450,
                elem_classes="sky-image-container"
            )

            # ── Watchlist Section ──
            gr.HTML("<div class='watchlist-section-title'>⭐ Watchlist</div>")
            watchlist_panel = gr.HTML(
                value=format_watchlist_html(memory_manager.get_watchlist()),
                elem_id="watchlist-panel"
            )

            # Add to Watchlist (compact form)
            with gr.Row():
                input_watch_target = gr.Textbox(
                    label="Target",
                    placeholder="e.g. TRAPPIST-1e",
                    scale=2,
                    lines=1
                )
                input_watch_notes = gr.Textbox(
                    label="Notes",
                    placeholder="e.g. Habitable zone",
                    scale=2,
                    lines=1
                )

            with gr.Row():
                btn_add_watch = gr.Button("＋ Add", size="sm", variant="secondary", scale=1)
                btn_remove_watch = gr.Button("－ Remove", size="sm", variant="secondary", scale=1)
                btn_refresh_watchlist = gr.Button("↻", size="sm", variant="secondary", scale=0)

            output_watch_status = gr.HTML(value="", elem_classes="status-toast")

    # ---------------------------------------------------------------------------
    # Event Handlers
    # ---------------------------------------------------------------------------

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

    btn_remove_watch.click(
        fn=remove_watchlist,
        inputs=[input_watch_target],
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
        inputs=[input_detail, input_survey, input_color_lut, input_fov],
        outputs=[output_prefs_status]
    )

import atexit

def cleanup_mcp_toolsets():
    logger.info("StarForge UI shutting down, closing all MCP connections...")
    try:
        from agents.literature_agent import arxiv_toolset
        logger.info("Closing arXiv toolset connection...")
        arxiv_toolset.close()
    except Exception as e:
        logger.warning(f"Error closing arxiv_toolset: {e}")

    try:
        from agents.query_agent import exoplanet_toolset, mast_toolset
        logger.info("Closing exoplanet and MAST toolset connections...")
        exoplanet_toolset.close()
        mast_toolset.close()
    except Exception as e:
        logger.warning(f"Error closing exoplanet/mast toolset: {e}")

    try:
        from agents.analysis_agent import skyview_toolset
        logger.info("Closing SkyView toolset connection...")
        skyview_toolset.close()
    except Exception as e:
        logger.warning(f"Error closing skyview_toolset: {e}")

atexit.register(cleanup_mcp_toolsets)

if __name__ == "__main__":
    app.launch(css=theme_css)
