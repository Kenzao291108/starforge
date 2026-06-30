"""
StarForge — NASA SkyView MCP Server

Provides tools to retrieve sky images from NASA's SkyView virtual observatory,
which provides access to survey data across the electromagnetic spectrum.

Data source: https://skyview.gsfc.nasa.gov
"""

import base64
import io
import logging
import os
import sys
from typing import Optional

import requests
from mcp.server.fastmcp import FastMCP

# Configure logging to stderr
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("skyview_mcp")

# SkyView API endpoint
SKYVIEW_URL = "https://skyview.gsfc.nasa.gov/current/cgi/runquery.pl"

# Initialize MCP server
mcp = FastMCP(
    "NASA SkyView",
    instructions="Retrieve multi-wavelength sky images from NASA's SkyView virtual observatory",
)

# Common survey descriptions
SURVEY_INFO = {
    "DSS": "Digitized Sky Survey (optical, visible light)",
    "DSS2 Red": "Digitized Sky Survey 2, Red band",
    "DSS2 Blue": "Digitized Sky Survey 2, Blue band",
    "DSS2 IR": "Digitized Sky Survey 2, Infrared band",
    "2MASS-J": "Two Micron All Sky Survey, J band (1.25μm)",
    "2MASS-H": "Two Micron All Sky Survey, H band (1.65μm)",
    "2MASS-K": "Two Micron All Sky Survey, K band (2.17μm)",
    "WISE 3.4": "Wide-field Infrared Survey Explorer, 3.4μm",
    "WISE 4.6": "Wide-field Infrared Survey Explorer, 4.6μm",
    "WISE 12": "Wide-field Infrared Survey Explorer, 12μm",
    "WISE 22": "Wide-field Infrared Survey Explorer, 22μm",
    "GALEX Near UV": "Galaxy Evolution Explorer, Near UV",
    "GALEX Far UV": "Galaxy Evolution Explorer, Far UV",
    "RASS": "ROSAT All-Sky Survey (X-ray)",
    "NVSS": "NRAO VLA Sky Survey (radio, 1.4 GHz)",
    "SDSS g": "Sloan Digital Sky Survey, g band",
    "SDSS r": "Sloan Digital Sky Survey, r band",
    "SDSS i": "Sloan Digital Sky Survey, i band",
}


def _get_skyview_image_url(
    target: str,
    survey: str = "DSS",
    pixels: Optional[int] = None,
    scale: Optional[float] = None,
) -> Optional[str]:
    """Get the URL of a SkyView image for a target.

    Args:
        target: Target name or coordinates
        survey: Survey name
        pixels: Image size in pixels (optional)
        scale: Image scale in degrees per pixel (optional)

    Returns:
        URL of the generated FITS/GIF image, or None on failure
    """
    params = {
        "Position": target,
        "Survey": survey,
        "Return": "GIF",
        "Projection": "Tan",
    }
    if pixels is not None:
        params["Pixels"] = str(pixels)
    if scale is not None:
        params["Size"] = str(scale)

    # Load color table preference from preferences.json if available
    pref_path = os.path.expanduser("~/.starforge/memory/preferences.json")
    if os.path.exists(pref_path):
        try:
            import json
            with open(pref_path, "r", encoding="utf-8") as f:
                prefs = json.load(f)
                color_lut = prefs.get("skyview_color_lut", "gray")
                if color_lut and color_lut not in ("gray", "grayscale", "Original/None"):
                    params["lut"] = color_lut
        except Exception as e:
            logger.warning(f"Failed to read preferences for SkyView color lut: {e}")


    try:
        logger.info(f"Requesting SkyView image: {target} ({survey})")
        response = requests.get(SKYVIEW_URL, params=params, timeout=30, allow_redirects=True)
        response.raise_for_status()

        # SkyView returns HTML with the image URL, or the image directly
        content_type = response.headers.get("Content-Type", "")

        if "image" in content_type:
            # Save direct image response to a local file in ~/.starforge/cache/
            cache_dir = os.path.expanduser("~/.starforge/cache")
            os.makedirs(cache_dir, exist_ok=True)
            # Create a safe filename from target and survey
            safe_target = "".join([c if c.isalnum() else "_" for c in target])
            safe_survey = "".join([c if c.isalnum() else "_" for c in survey])
            filename = f"skyview_{safe_target}_{safe_survey}.gif"
            filepath = os.path.join(cache_dir, filename)
            try:
                with open(filepath, "wb") as f:
                    f.write(response.content)
                logger.info(f"Saved direct SkyView image response to local cache: {filepath}")
                return f"file://{filepath}"
            except Exception as e:
                logger.error(f"Failed to save SkyView image to local cache: {e}")
                # Fallback to base64 only if saving fails
                img_b64 = base64.b64encode(response.content).decode("utf-8")
                return f"data:image/gif;base64,{img_b64}"

        if "text/html" in content_type:
            # Parse HTML for image URL
            html = response.text
            # Look for the image URL in the response
            import re
            img_match = re.search(r'<img[^>]+src=["\']([^"\']*tempspace[^"\']*)["\']', html)
            if img_match:
                img_url = img_match.group(1)
                if not img_url.startswith("http"):
                    img_url = f"https://skyview.gsfc.nasa.gov{img_url}"
                return img_url

            # Alternative: look for FITS or GIF links
            link_match = re.search(r'href=["\']([^"\']*\.gif)["\']', html, re.IGNORECASE)
            if link_match:
                link = link_match.group(1)
                if not link.startswith("http"):
                    link = f"https://skyview.gsfc.nasa.gov{link}"
                return link

        logger.warning(f"Could not extract image URL from SkyView response")
        return None

    except requests.exceptions.RequestException as e:
        logger.error(f"SkyView request failed: {e}")
        return None


def _query_mast_for_previews(target: str) -> Optional[dict]:
    """Helper to query MAST for observations of a target with a jpegURL.
    
    Uses Mast.Caom.Cone (much faster coordinate lookup) and filters client-side.
    """
    import requests
    import json

    # Step 1: Resolve target name to coordinates (using GET — faster)
    ra, dec = None, None
    try:
        resolve_response = requests.get(
            "https://mast.stsci.edu/api/v0/invoke",
            params={
                "request": f'{{"service":"Mast.Name.Lookup","params":{{"input":"{target}","format":"json"}}}}'
            },
            timeout=10,
        )
        resolve_data = resolve_response.json()
        if resolve_data and "resolvedCoordinate" in resolve_data and resolve_data["resolvedCoordinate"]:
            coord = resolve_data["resolvedCoordinate"][0]
            ra = coord.get("ra")
            dec = coord.get("decl")
            logger.info(f"MAST resolved '{target}' to RA={ra}, Dec={dec}")
    except Exception as e:
        logger.warning(f"MAST name resolution failed for '{target}': {e}")

    # Fallback to Simbad (via astroquery) if MAST name resolution fails
    if ra is None or dec is None:
        try:
            logger.info(f"Attempting Simbad name resolution fallback for '{target}'...")
            from astroquery.simbad import Simbad
            simbad_res = Simbad.query_object(target)
            if simbad_res and len(simbad_res) > 0:
                ra = float(simbad_res['ra'][0])
                dec = float(simbad_res['dec'][0])
                logger.info(f"Simbad resolved '{target}' to RA={ra}, Dec={dec}")
        except Exception as simbad_err:
            logger.warning(f"Simbad name resolution fallback failed for '{target}': {simbad_err}")

    # Step 2: Build the search request
    if ra is not None and dec is not None:
        # Standard cone search service - extremely fast for coordinates
        request_obj = {
            "service": "Mast.Caom.Cone",
            "format": "json",
            "pagesize": 150,
            "page": 1,
            "params": {
                "ra": ra,
                "dec": dec,
                "radius": 0.03
            }
        }
    else:
        # Fallback to text-based target_name search using Caom.Filtered
        filters = [{"paramName": "target_name", "values": [], "freeText": f"%{target}%"}]
        request_obj = {
            "service": "Mast.Caom.Filtered",
            "format": "json",
            "pagesize": 100,
            "page": 1,
            "params": {
                "columns": "*",
                "filters": filters
            }
        }

    search_params = {"request": json.dumps(request_obj)}

    results = []
    # Progressive timeouts to give slow MAST queries more time to complete
    timeouts = [12.0, 16.0, 20.0]
    max_attempts = len(timeouts)
    import time

    for attempt in range(1, max_attempts + 1):
        timeout_val = timeouts[attempt - 1]
        try:
            logger.info(f"Querying MAST (attempt {attempt}/{max_attempts}, timeout={timeout_val}s)...")
            response = requests.post(
                "https://mast.stsci.edu/api/v0/invoke",
                data=search_params,
                headers={
                    "Content-type": "application/x-www-form-urlencoded",
                    "Accept": "text/plain",
                },
                timeout=timeout_val,
            )
            response.raise_for_status()
            data = response.json()
            results = data.get("data", [])
            break  # Success, exit retry loop
        except Exception as e:
            logger.warning(f"MAST query attempt {attempt} failed: {e}")
            if attempt < max_attempts:
                time.sleep(1.0)
            else:
                logger.error(f"All {max_attempts} MAST query attempts failed.")
                return None

    logger.info(f"MAST returned {len(results)} observations for '{target}'")

    # Pass 1: prefer actual 2D images from JWST > HST/HLA > others
    preview_obs = None
    for obs in results:
        if obs.get("jpegURL") and obs.get("dataproduct_type", "").lower() == "image":
            mission = obs.get("obs_collection", "").upper()
            if "JWST" in mission:
                return obs
            elif "HST" in mission or "HLA" in mission or "HUBBLE" in mission:
                if not preview_obs or "JWST" not in preview_obs.get("obs_collection", "").upper():
                    preview_obs = obs
            elif not preview_obs:
                preview_obs = obs
    if preview_obs:
        return preview_obs

    # Pass 2: fall back to any observation with a jpegURL
    preview_obs = None
    for obs in results:
        if obs.get("jpegURL"):
            mission = obs.get("obs_collection", "").upper()
            if "JWST" in mission:
                return obs
            elif "HST" in mission or "HLA" in mission or "HUBBLE" in mission:
                if not preview_obs or "JWST" not in preview_obs.get("obs_collection", "").upper():
                    preview_obs = obs
            elif not preview_obs:
                preview_obs = obs

    return preview_obs


@mcp.tool()
def get_sky_image(
    target: str,
    survey: str = "",
    size_arcmin: Optional[float] = None,
) -> str:
    """Get a sky image of an astronomical target.

    The survey is determined by the user's saved preferences.
    Do NOT pass the survey argument — it is read automatically from settings.

    Args:
        target: Target name (e.g., 'TRAPPIST-1', 'M31', 'NGC 7293') or
                coordinates (e.g., '23.46 30.66' for RA Dec in degrees).
        survey: IGNORED — always overridden by user preferences. Do not set this.
        size_arcmin: Field of view in arcminutes (optional).

    Returns:
        URL to the sky image and metadata about the observation.
    """
    # ALWAYS read survey from preferences — ignore what the LLM passes
    pref_path = os.path.expanduser("~/.starforge/memory/preferences.json")
    effective_survey = "DSS"
    lut_param = ""
    pref_size_arcmin = None
    if os.path.exists(pref_path):
        try:
            import json
            with open(pref_path, "r", encoding="utf-8") as f:
                prefs = json.load(f)
                effective_survey = prefs.get("default_survey", "DSS")
                color_lut = prefs.get("skyview_color_lut", "gray")
                pref_size_arcmin = prefs.get("skyview_fov_arcmin", None)
                if color_lut and color_lut not in ("gray", "grayscale", "Original/None"):
                    lut_param = f"&lut={color_lut}"
        except Exception:
            pass

    if size_arcmin is None and pref_size_arcmin is not None:
        try:
            size_arcmin = float(pref_size_arcmin)
        except (ValueError, TypeError):
            pass

    logger.info(f"User preferred survey: {effective_survey}")

    # ── MAST path: query real observatory images ──
    if effective_survey in ("JWST/HST (MAST)", "MAST"):
        preview_obs = _query_mast_for_previews(target)
        if preview_obs and preview_obs.get("jpegURL"):
            obs_collection = preview_obs.get("obs_collection", "Space Telescope")
            inst = preview_obs.get("instrument_name", "Unknown Instrument")
            title = preview_obs.get("obs_title", "High-resolution targeted observation")
            raw_url = preview_obs.get("jpegURL")
            # Convert internal mast: URIs to full download URLs
            if raw_url.startswith("mast:"):
                jpeg_url = f"https://mast.stsci.edu/api/v0.1/Download/file?uri={raw_url}"
            else:
                jpeg_url = raw_url

            output = f"## Sky Image: {target} (High-Resolution Observatory View)\n\n**Source:** {obs_collection} — {inst}\n**Description:** {title}\n**Image URL:** {jpeg_url}\n\n### Direct MAST Link\n[View on MAST]({jpeg_url})\n\n*Data source: Mikulski Archive for Space Telescopes (MAST) (https://archive.stsci.edu)*\n"
            return output

        # If MAST had no preview, fall back to DSS via SkyView
        logger.info("No MAST preview found, falling back to SkyView DSS")
        effective_survey = "DSS"

    # ── SkyView path: fetch from the chosen survey ──
    scale_param = ""
    fov_text = "Default (Survey Native)"
    scale = None
    if size_arcmin is not None:
        scale = size_arcmin / 60.0
        scale_param = f"&Pixels=500&Size={scale}"
        fov_text = f"{size_arcmin} arcminutes"

    image_url = _get_skyview_image_url(target, survey=effective_survey, scale=scale)
    survey_desc = SURVEY_INFO.get(effective_survey, effective_survey)

    if image_url:
        output = f"## Sky Image: {target}\n\n**Survey:** {effective_survey} — {survey_desc}\n**Field of View:** {fov_text}\n**Image URL:** {image_url}\n\n### Direct SkyView Link\n[View on SkyView](https://skyview.gsfc.nasa.gov/current/cgi/runquery.pl?Position={target.replace(' ', '+')}&Survey={effective_survey.replace(' ', '+')}&Return=GIF{scale_param}{lut_param}&Projection=Tan)\n\n*Data source: NASA SkyView (https://skyview.gsfc.nasa.gov)*\n"
    else:
        output = f"## Sky Image: {target}\n\n**Survey:** {effective_survey} — {survey_desc}\n\n⚠️ Could not retrieve image directly. You can view it manually:\n[Open in SkyView](https://skyview.gsfc.nasa.gov/current/cgi/runquery.pl?Position={target.replace(' ', '+')}&Survey={effective_survey.replace(' ', '+')}&Return=GIF{scale_param}{lut_param}&Projection=Tan)\n\n*Data source: NASA SkyView (https://skyview.gsfc.nasa.gov)*\n"
    return output


@mcp.tool()
def get_multi_wavelength(
    target: str,
    size_arcmin: float = 15.0,
) -> str:
    """Get sky images of a target across multiple wavelengths (radio to X-ray).

    This is useful for understanding the multi-wavelength properties of an
    astronomical object. Returns images from optical, infrared, UV, and X-ray surveys.

    Args:
        target: Target name or coordinates.
        size_arcmin: Field of view in arcminutes (default: 15.0).

    Returns:
        URLs to sky images from multiple surveys spanning the electromagnetic spectrum.
    """
    scale = size_arcmin / 60.0

    # Select representative surveys across the spectrum
    surveys = [
        ("GALEX Far UV", "Far Ultraviolet"),
        ("DSS", "Optical (Visible Light)"),
        ("2MASS-J", "Near Infrared (1.25μm)"),
        ("WISE 3.4", "Mid Infrared (3.4μm)"),
    ]

    output_lines = [
        f"## Multi-Wavelength View: {target}\n",
        f"**Field of View:** {size_arcmin} arcminutes\n",
        "Images across the electromagnetic spectrum:\n",
    ]

    for survey_name, label in surveys:
        image_url = _get_skyview_image_url(target, survey=survey_name, scale=scale)

        skyview_link = (
            f"https://skyview.gsfc.nasa.gov/current/cgi/runquery.pl?"
            f"Position={target.replace(' ', '+')}&Survey={survey_name.replace(' ', '+')}"
            f"&Return=GIF&Pixels=400&Size={scale}&Projection=Tan"
        )

        if image_url:
            output_lines.append(f"### {label} ({survey_name})")
            output_lines.append(f"**Image:** {image_url}")
            output_lines.append(f"[View full resolution]({skyview_link})")
            output_lines.append("")
        else:
            output_lines.append(f"### {label} ({survey_name})")
            output_lines.append(f"[View on SkyView]({skyview_link})")
            output_lines.append("")

    output_lines.append(
        "*Data source: NASA SkyView (https://skyview.gsfc.nasa.gov)*"
    )
    return "\n".join(output_lines)


@mcp.tool()
def list_available_surveys() -> str:
    """List the most commonly used sky surveys available in SkyView.

    Returns:
        A categorized list of surveys with descriptions and wavelength information.
    """
    output = """## Available SkyView Surveys

### Optical (Visible Light)
| Survey | Description |
|--------|-------------|
| DSS | Digitized Sky Survey (blue+red combined) |
| DSS2 Red | DSS 2nd generation, Red plate |
| DSS2 Blue | DSS 2nd generation, Blue plate |
| DSS2 IR | DSS 2nd generation, Infrared plate |
| SDSS g | Sloan Digital Sky Survey, g band (480nm) |
| SDSS r | Sloan Digital Sky Survey, r band (625nm) |
| SDSS i | Sloan Digital Sky Survey, i band (770nm) |

### Infrared
| Survey | Description |
|--------|-------------|
| 2MASS-J | Two Micron All Sky Survey, J band (1.25μm) |
| 2MASS-H | Two Micron All Sky Survey, H band (1.65μm) |
| 2MASS-K | Two Micron All Sky Survey, K band (2.17μm) |
| WISE 3.4 | WISE, 3.4μm |
| WISE 4.6 | WISE, 4.6μm |
| WISE 12 | WISE, 12μm |
| WISE 22 | WISE, 22μm |

### Ultraviolet
| Survey | Description |
|--------|-------------|
| GALEX Near UV | GALEX Near UV (177-283nm) |
| GALEX Far UV | GALEX Far UV (135-175nm) |

### X-ray
| Survey | Description |
|--------|-------------|
| RASS | ROSAT All-Sky Survey (0.1-2.4 keV) |

### Radio
| Survey | Description |
|--------|-------------|
| NVSS | NRAO VLA Sky Survey (1.4 GHz) |

*Data source: NASA SkyView (https://skyview.gsfc.nasa.gov)*
"""
    return output


# Entry point
if __name__ == "__main__":
    logger.info("Starting NASA SkyView MCP Server...")
    mcp.run(transport="stdio")
