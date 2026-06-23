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
                if color_lut and color_lut not in ("gray", "grayscale"):
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
            # Direct image response — encode as base64 data URL
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
    """Helper to query MAST for observations of a target with a jpegURL."""
    import requests
    import json
    
    search_url = "https://mast.stsci.edu/api/v0/invoke"
    search_params = {
        "request": json.dumps({
            "service": "Mast.Caom.Filtered.Position",
            "params": {
                "columns": "*",
                "filters": [],
                "position": target,
                "radius": 0.01,
                "pagesize": 50,
                "page": 1,
                "format": "json"
            }
        })
    }
    
    try:
        response = requests.post(
            search_url,
            data=search_params,
            headers={
                "Content-type": "application/x-www-form-urlencoded",
                "Accept": "text/plain",
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("data", [])
        if not results:
            return None
            
        # Prioritize JWST, then HST/Hubble, then others
        preview_obs = None
        for obs in results:
            if obs.get("jpegURL"):
                mission = obs.get("obs_collection", "").upper()
                if "JWST" in mission:
                    return obs  # JWST is top priority, return immediately
                elif "HST" in mission or "HUBBLE" in mission:
                    if not preview_obs or "JWST" not in preview_obs.get("obs_collection", "").upper():
                        preview_obs = obs
                elif not preview_obs:
                    preview_obs = obs
                    
        return preview_obs
    except Exception as e:
        logger.warning(f"MAST query failed in SkyView helper: {e}")
        return None


@mcp.tool()
def get_sky_image(
    target: str,
    survey: str = "DSS",
    size_arcmin: Optional[float] = None,
) -> str:
    """Get a sky image of an astronomical target from a specific survey.

    Args:
        target: Target name (e.g., 'TRAPPIST-1', 'M31', 'NGC 1277') or
                coordinates (e.g., '23.46 30.66' for RA Dec in degrees).
        survey: Survey name (default: 'DSS'). Common options:
                'DSS' (optical), '2MASS-J' (near-IR), 'WISE 3.4' (mid-IR),
                'GALEX Near UV' (ultraviolet), 'RASS' (X-ray).
        size_arcmin: Field of view in arcminutes (optional, uses survey default if not specified).

    Returns:
        URL to the sky image and metadata about the observation.
    """
    # Load default survey and color table preference
    pref_path = os.path.expanduser("~/.starforge/memory/preferences.json")
    pref_survey = "DSS"
    lut_param = ""
    if os.path.exists(pref_path):
        try:
            import json
            with open(pref_path, "r", encoding="utf-8") as f:
                prefs = json.load(f)
                pref_survey = prefs.get("default_survey", "DSS")
                color_lut = prefs.get("skyview_color_lut", "gray")
                if color_lut and color_lut not in ("gray", "grayscale"):
                    lut_param = f"&lut={color_lut}"
        except Exception:
            pass

    # Override survey if it is the default DSS but the user preferred a different one
    if survey == "DSS" and pref_survey != "DSS":
        survey = pref_survey

    # Map JWST/HST (MAST) preference to a valid SkyView fallback (DSS)
    skyview_survey = survey
    if skyview_survey == "JWST/HST (MAST)":
        # Prioritize MAST targeted high-resolution preview image
        preview_obs = _query_mast_for_previews(target)
        if preview_obs and preview_obs.get("jpegURL"):
            obs_collection = preview_obs.get("obs_collection", "Space Telescope")
            inst = preview_obs.get("instrument_name", "Unknown Instrument")
            title = preview_obs.get("obs_title", "High-resolution targeted observation")
            jpeg_url = preview_obs.get("jpegURL")
            
            output = f"""## Sky Image: {target} (High-Resolution View)

**Observatory:** {obs_collection}
**Instrument:** {inst}
**Description:** {title}
**Image URL:** {jpeg_url}

### Direct MAST Link
[View on MAST]({jpeg_url})

*Data source: Mikulski Archive for Space Telescopes (MAST) (https://archive.stsci.edu)*
"""
            return output
            
        # Fallback to DSS in SkyView if no MAST preview was found
        skyview_survey = "DSS"

    scale_param = ""
    fov_text = "Default (Survey Native)"
    scale = None
    if size_arcmin is not None:
        scale = size_arcmin / 60.0  # Convert arcminutes to degrees
        scale_param = f"&Pixels=500&Size={scale}"
        fov_text = f"{size_arcmin} arcminutes"

    image_url = _get_skyview_image_url(target, survey=skyview_survey, scale=scale)

    survey_desc = SURVEY_INFO.get(skyview_survey, skyview_survey)

    if image_url:
        output = f"""## Sky Image: {target}

**Survey:** {survey} — {survey_desc}
**Field of View:** {fov_text}
**Image URL:** {image_url}

### Direct SkyView Link
[View on SkyView](https://skyview.gsfc.nasa.gov/current/cgi/runquery.pl?Position={target.replace(' ', '+')}&Survey={survey.replace(' ', '+')}&Return=GIF{scale_param}{lut_param}&Projection=Tan)

*Data source: NASA SkyView (https://skyview.gsfc.nasa.gov)*
"""
    else:
        output = f"""## Sky Image: {target}

**Survey:** {survey} — {survey_desc}

⚠️ Could not retrieve image directly. You can view it manually:
[Open in SkyView](https://skyview.gsfc.nasa.gov/current/cgi/runquery.pl?Position={target.replace(' ', '+')}&Survey={survey.replace(' ', '+')}&Return=GIF{scale_param}{lut_param}&Projection=Tan)

*Data source: NASA SkyView (https://skyview.gsfc.nasa.gov)*
"""
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
