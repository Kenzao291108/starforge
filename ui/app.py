import os
import re
import sys
import json
import uuid
import base64
import html
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

js_script = """
<script>
(function() {
    function syncBg() {
        const skyImgContainers = document.querySelectorAll('.sky-image-container');
        skyImgContainers.forEach(container => {
            const img = container.querySelector('img');
            const frame = container.querySelector('.image-frame') || container.querySelector('.image-container');
            if (img && frame) {
                const src = img.src;
                if (frame.style.backgroundImage !== `url("${src}")`) {
                    frame.style.backgroundImage = `url("${src}")`;
                }
            }
        });
    }

    const observer = new MutationObserver(() => {
        syncBg();
    });

    const interval = setInterval(() => {
        if (document.body) {
            clearInterval(interval);
            observer.observe(document.body, { childList: true, subtree: true });
            syncBg();
        }
    }, 100);
})();

window.deleteHistoryItem = function(timestamp, event) {
    if (event) {
        event.stopPropagation();
        event.preventDefault();
    }
    const btn = event ? event.target.closest('.delete-history-btn') : null;
    const historyItem = btn ? btn.closest('.history-item') : null;
    if (historyItem) {
        historyItem.style.transition = 'all 0.4s ease';
        historyItem.style.opacity = '0';
        historyItem.style.transform = 'translateX(20px)';
        setTimeout(() => { historyItem.remove(); }, 400);
    }
    window.tempHistoryDeleteVal = timestamp;
    const gradioBtn = document.querySelector('#btn-delete-history');
    if (gradioBtn) {
        setTimeout(() => { gradioBtn.click(); }, 50);
    }
};

window.deleteWatchlistItem = function(targetName, event) {
    if (event) {
        event.stopPropagation();
        event.preventDefault();
    }
    const btn = event ? event.target.closest('.delete-watchlist-btn') : null;
    const watchlistCard = btn ? btn.closest('.watchlist-card') : null;
    if (watchlistCard) {
        watchlistCard.style.transition = 'all 0.4s ease';
        watchlistCard.style.opacity = '0';
        watchlistCard.style.transform = 'translateX(20px)';
        setTimeout(() => { watchlistCard.remove(); }, 400);
    }
    window.tempWatchlistDeleteVal = targetName;
    const gradioBtn = document.querySelector('#btn-delete-watchlist');
    if (gradioBtn) {
        setTimeout(() => { gradioBtn.click(); }, 50);
    }
};
</script>
"""

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
            # SECURITY: restrict file paths to ~/.starforge or workspace
            allowed_dirs = [
                os.path.abspath(os.path.expanduser("~/.starforge")),
                os.path.abspath(BASE_DIR)
            ]
            abs_path = os.path.abspath(path)
            if not any(abs_path.startswith(d) for d in allowed_dirs):
                logger.error(f"[Image] Security violation: Attempted to load unauthorized local file {path}")
                return None
            
            if os.path.exists(abs_path):
                return Image.open(abs_path)
            else:
                logger.error(f"[Image] Local file not found: {abs_path}")
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
        <div class="watchlist-empty">
            <div class="watchlist-empty-icon">⭐</div>
            <div class="watchlist-empty-title">No systems in watchlist yet.</div>
            <div class="watchlist-empty-subtitle">Analyze a target and add it here to track.</div>
        </div>"""

    html_str = "<div class='watchlist-list'>"
    for idx, item in enumerate(watchlist):
        notes_html = ""
        if item.get("notes"):
            safe_notes = html.escape(item['notes'][:80])
            notes_html = f"<div class='watchlist-notes'>{safe_notes}</div>"

        safe_target = html.escape(item['target_name'])
        safe_target_js = item['target_name'].replace(chr(39), "")
        safe_timestamp = html.escape(item['timestamp'][:10])

        html_str += f"""
        <div class="watchlist-card">
            <div class="watchlist-card-header">
                <div class="watchlist-item-content">
                    <span class="watchlist-icon">🪐</span>
                    <span class="watchlist-target-name">{safe_target}</span>
                </div>
                <div class="watchlist-actions">
                    <span class="watchlist-timestamp">{safe_timestamp}</span>
                    <button class="delete-watchlist-btn" onclick="deleteWatchlistItem('{safe_target_js}', event)">( - )</button>
                </div>
            </div>
            {notes_html}
        </div>"""

    html_str += "</div>"
    return html_str


def format_history_html(history):
    """Render history as compact clickable items for the sidebar."""
    if not history:
        return """
        <div class="history-empty">
            <div class="history-empty-icon">🔭</div>
            <div class="history-empty-title">No research yet.</div>
            <div class="history-empty-subtitle">Start by analyzing a target system.</div>
        </div>"""

    html_str = "<div class='history-list'>"
    for idx, item in enumerate(history):
        target_disp = item.get('target_name') or item.get('query', 'Unknown')
        query_snippet = item.get('query', '')[:45]
        date_str = item.get('timestamp', '')[:10]
        
        safe_target_disp = html.escape(target_disp)
        safe_query_snippet = html.escape(query_snippet)
        safe_date_str = html.escape(date_str)
        
        js_query_snippet = query_snippet.replace(chr(39), "").replace(chr(10), "").replace(chr(13), "")

        html_str += f"""
        <div class="history-item" onclick="
            document.querySelector('#search-input textarea').value = '{js_query_snippet}';
            document.querySelector('#search-input textarea').dispatchEvent(new Event('input', {{bubbles: true}}));
        ">
            <div class="history-item-header">
                <div class="history-item-content">
                    <span class="history-bullet">●</span>
                    <span class="history-target-name">{safe_target_disp}</span>
                </div>
                <button class="delete-history-btn" onclick="deleteHistoryItem('{item.get('timestamp')}', event)">( - )</button>
            </div>
            <div class="history-query">{safe_query_snippet}…</div>
            <div class="history-date">{safe_date_str}</div>
        </div>"""

    html_str += "</div>"
    return html_str


# ---------------------------------------------------------------------------
# Core research function
# ---------------------------------------------------------------------------

async def run_research(query, session_id):
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
            async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=new_message):
                events.append(event)
            break  # Success, exit retry loop
        except Exception as e:
            err_msg = str(e)
            is_transient = any(
                err in err_msg.lower()
                for err in (
                    "503", "unavailable", "429", "resource_exhausted", "limit",
                    "connect", "connection", "timeout", "name resolution", "dns",
                    "getaddrinfo", "11001", "socket", "network"
                )
            )
            if is_transient and attempt < max_retries:
                import asyncio
                logger.warning(
                    f"Transient or network error (attempt {attempt + 1}/{max_retries + 1}): {err_msg}. "
                    f"Retrying in {retry_delay}s..."
                )
                await asyncio.sleep(retry_delay)
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

    # Clean up runner explicitly to prevent ADK stdio session resource leaks
    try:
        await runner.close()
    except Exception as e:
        logger.warning(f"Error closing runner: {e}")

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
import os
import base64

# Load CSS themes
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(base_dir, "ui", "dark_theme.css"), "r", encoding="utf-8") as f:
    dark_css = f.read()
    
with open(os.path.join(base_dir, "ui", "light_theme.css"), "r", encoding="utf-8") as f:
    light_css = f.read()


theme_css = dark_css + "\n" + light_css + """
/* Remove Gradio's default gray background on form containers, but preserve our custom panels */
body.dark .gr-form:not(.glass-panel):not(.watchlist-section),
body.dark .gr-group:not(.glass-panel):not(.watchlist-section),
body.dark .gr-box:not(.glass-panel):not(.watchlist-section),
body:not(.dark) .gr-form:not(.glass-panel):not(.watchlist-section),
body:not(.dark) .gr-group:not(.glass-panel):not(.watchlist-section),
body:not(.dark) .gr-box:not(.glass-panel):not(.watchlist-section) {
    background-color: transparent !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
}

/* Remove backdrop blur on Gradio settings dialogs/modals */
dialog::backdrop,
.dialog-container,
.dialog,
.modal,
.modal-backdrop {
    backdrop-filter: none !important;
    -webkit-backdrop-filter: none !important;
}

/* Custom styling for delete buttons inside history and watchlist */
.delete-history-btn, .delete-watchlist-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 30px;
    height: 100%;
    color: #ff3c3c !important;
    background: transparent !important;
    border: none !important;
    cursor: pointer !important;
    flex-shrink: 0; /* Prevents button from collapsing */
    transition: transform 0.2s ease !important;
}

.delete-history-btn:hover, .delete-watchlist-btn:hover {
    transform: scale(1.1) !important;
    background: rgba(255, 60, 60, 0.1) !important;
    border-radius: 4px !important;
}

/* Ensure text truncation doesn't push the button out of view */
.history-item-content, .watchlist-item-content {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

/* Hide delete triggers from view, while leaving them rendered for JS click */
#btn-delete-history, #btn-delete-watchlist {
    display: none !important;
}
"""

# ---------------------------------------------------------------------------
# Build the Gradio Blocks UI
# ---------------------------------------------------------------------------

with gr.Blocks(title="StarForge — Exoplanet Research Assistant") as app:
    session_state = gr.State("")

    # Hidden components for deletion events from custom HTML
    hidden_delete_history_target = gr.Textbox(visible=False, elem_id="hidden-delete-history-target")
    hidden_delete_watchlist_target = gr.Textbox(visible=False, elem_id="hidden-delete-watchlist-target")
    btn_delete_history = gr.Button(visible=True, elem_id="btn-delete-history")
    btn_delete_watchlist = gr.Button(visible=True, elem_id="btn-delete-watchlist")

    # ── Left Sidebar: Conversation History ──
    with gr.Sidebar(label="🔭 StarForge", position="left", width=300, open=True):
        gr.HTML("""
            <div class="sidebar-brand">
                <h2>StarForge</h2>
                <div class="main-subtitle" style="font-size: 0.95em; margin-top: 4px;">Exoplanet Research AI</div>
            </div>
        """)
        btn_refresh_history = gr.Button("↻ Refresh History", size="sm", variant="secondary")

        gr.HTML("<div class='sidebar-section-label'>Research History</div>")
        history_panel = gr.HTML(
            value=format_history_html(memory_manager.get_history()),
            elem_id="history-panel"
        )

    # ── Main Content Area ──

    # Header
    gr.HTML("""
        <div class="main-header" style="text-align: center; margin-bottom: 24px;">
            <h1 style="font-family: 'Outfit', sans-serif; font-weight: 900; font-size: 2.8em; background: linear-gradient(135deg, #e040fb 0%, #7c8aff 40%, #00e5ff 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin: 0;">🔭 StarForge Agent</h1>
            <div class="main-subtitle" style="color: #9ca3c4; font-size: 1.1em; font-weight: 400; margin-top: 4px;">AI-powered Multi-Agent Exoplanet Research Assistant</div>
        </div>
    """)

    with gr.Tabs():
        with gr.Tab("Dashboard"):
            # Search Bar and Suggestions in a Panel
            with gr.Group(elem_classes="glass-panel"):
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
                        elem_classes="report-output",
                        latex_delimiters=[
                            {"left": "$$", "right": "$$", "display": True},
                            {"left": "$", "right": "$", "display": False}
                        ]
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
                    with gr.Group(elem_classes="watchlist-section"):
                        gr.HTML("<div class='watchlist-section-title'>⭐ Watchlist</div>")
                        watchlist_panel = gr.HTML(
                            value=format_watchlist_html(memory_manager.get_watchlist()),
                            elem_id="watchlist-panel"
                        )

                        # Add to Watchlist (compact form)
                        gr.HTML("<div class='watchlist-section-title' style='margin-top: 15px;'>Add to Watchlist</div>")
                        input_watch_target = gr.Textbox(
                            label="Target",
                            placeholder="e.g. TRAPPIST-1e",
                            lines=1
                        )
                        input_watch_notes = gr.Textbox(
                            label="Notes",
                            placeholder="e.g. Habitable zone",
                            lines=1
                        )

                        with gr.Row(equal_height=True):
                            btn_add_watch = gr.Button("＋ Add to Watchlist", size="sm", variant="secondary", scale=3, min_width=100, elem_classes="btn-neon-add")
                            btn_refresh_watchlist = gr.Button("↻", size="sm", variant="secondary", scale=1, min_width=30)

                        output_watch_status = gr.HTML(value="", elem_classes="status-toast")

        with gr.Tab("Settings"):
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
                    maximum=300,
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

    # ---------------------------------------------------------------------------
    # Event Handlers
    # ---------------------------------------------------------------------------

    def show_loading():
        loading_msg = "### ⏳ Analyzing Target...\n\nPlease patiently wait while the AI agents conduct their research. This may take a minute.\n\n> **Note:** Sometimes detailed image data from recent missions may be unavailable or locked due to the Exclusive Access Period (EAP)."
        return loading_msg, None
        
    btn_submit.click(
        fn=show_loading,
        inputs=None,
        outputs=[output_report, output_image]
    ).then(
        fn=run_research,
        inputs=[input_query, session_state],
        outputs=[output_report, output_image, session_state, history_panel, input_watch_target]
    )

    input_query.submit(
        fn=show_loading,
        inputs=None,
        outputs=[output_report, output_image]
    ).then(
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
        inputs=[input_detail, input_survey, input_color_lut, input_fov],
        outputs=[output_prefs_status]
    )

    # Deletion callbacks
    def delete_history_item(timestamp):
        memory_manager.remove_from_history(timestamp)
        return format_history_html(memory_manager.get_history())

    def delete_watchlist_item(target_name):
        memory_manager.remove_from_watchlist(target_name)
        return format_watchlist_html(memory_manager.get_watchlist())

    btn_delete_history.click(
        fn=delete_history_item,
        inputs=[hidden_delete_history_target],
        outputs=[history_panel],
        js="() => { return window.tempHistoryDeleteVal || ''; }"
    )

    btn_delete_watchlist.click(
        fn=delete_watchlist_item,
        inputs=[hidden_delete_watchlist_target],
        outputs=[watchlist_panel],
        js="() => { return window.tempWatchlistDeleteVal || ''; }"
    )

import atexit

def cleanup_mcp_toolsets():
    logger.info("StarForge UI shutting down, closing all MCP connections...")
    import asyncio
    
    async def do_cleanup():
        try:
            from agents.literature_agent import arxiv_toolset
            logger.info("Closing arXiv toolset connection...")
            await arxiv_toolset.close()
        except Exception as e:
            logger.warning(f"Error closing arxiv_toolset: {e}")

        try:
            from agents.query_agent import exoplanet_toolset, mast_toolset
            logger.info("Closing exoplanet and MAST toolset connections...")
            await exoplanet_toolset.close()
            await mast_toolset.close()
        except Exception as e:
            logger.warning(f"Error closing exoplanet/mast toolset: {e}")

        try:
            from agents.analysis_agent import skyview_toolset
            logger.info("Closing SkyView toolset connection...")
            await skyview_toolset.close()
        except Exception as e:
            logger.warning(f"Error closing skyview_toolset: {e}")
            
    try:
        asyncio.run(do_cleanup())
    except Exception as e:
        logger.warning(f"Error during async cleanup: {e}")

atexit.register(cleanup_mcp_toolsets)

if __name__ == "__main__":
    app.launch(css=theme_css, head=js_script)
