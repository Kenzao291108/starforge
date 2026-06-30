"""
StarForge — MAST (Mikulski Archive for Space Telescopes) MCP Server

Provides tools to query the MAST archive for observation data from
missions like Hubble, TESS, JWST, and Kepler.

Data source: https://archive.stsci.edu
API docs: https://mast.stsci.edu/api/v0/
"""

import logging
import sys
from typing import Optional

import requests
from mcp.server.fastmcp import FastMCP

# Configure logging to stderr (required for stdio MCP transport)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mast_archive_mcp")

# MAST API endpoints
MAST_API_URL = "https://mast.stsci.edu/api/v0/invoke"
MAST_RESOLVER_URL = "https://mast.stsci.edu/api/v0/invoke"

# Initialize MCP server
mcp = FastMCP(
    "MAST Archive",
    instructions="Query the Mikulski Archive for Space Telescopes (MAST) for Hubble, TESS, JWST, and Kepler data",
)


def _mast_query(service: str, params: dict, timeout: int = 30) -> dict:
    """Execute a query against the MAST API.

    Args:
        service: The MAST service to query
        params: Query parameters
        timeout: Request timeout in seconds

    Returns:
        JSON response from MAST
    """
    request_payload = {"service": service, "params": params, "format": "json"}

    headers = {"Content-type": "application/x-www-form-urlencoded", "Accept": "text/plain"}

    try:
        logger.info(f"Querying MAST service: {service}")
        response = requests.post(
            MAST_API_URL,
            data={"request": str(request_payload).replace("'", '"')},
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"MAST query failed: {e}")
        return {"error": str(e)}
    except ValueError:
        logger.error("Failed to parse MAST response")
        return {"error": "Failed to parse response from MAST"}


def _mast_filtered_query(
    target: str,
    mission: Optional[str] = None,
    radius: float = 0.001,
    max_results: int = 20,
) -> list[dict]:
    """Query MAST for observations using the cone search API.

    Uses the simpler MAST query interface with requests library.
    """
    # Use the MAST portal search API
    base_url = "https://mast.stsci.edu/api/v0.1/resolver"

    # First resolve the target name to coordinates
    try:
        resolve_response = requests.get(
            f"https://mast.stsci.edu/api/v0/invoke",
            params={
                "request": f'{{"service":"Mast.Name.Lookup","params":{{"input":"{target}","format":"json"}}}}'
            },
            timeout=15,
        )
        resolve_data = resolve_response.json()
    except Exception as e:
        logger.warning(f"Name resolution failed: {e}, trying direct catalog search")
        resolve_data = None

    ra, dec = None, None
    if resolve_data and "resolvedCoordinate" in resolve_data and resolve_data["resolvedCoordinate"]:
        coord = resolve_data["resolvedCoordinate"][0]
        ra = coord.get("ra")
        dec = coord.get("decl")

    import json
    search_url = "https://mast.stsci.edu/api/v0/invoke"
    
    filters = []
    if mission:
        filters.append({"paramName": "obs_collection", "values": [mission]})

    if ra is not None and dec is not None:
        # Use standard CAOM Cone search but with filtering via Mast.Caom.Filtered.Position
        request_obj = {
            "service": "Mast.Caom.Filtered.Position",
            "format": "json",
            "pagesize": max_results,
            "page": 1,
            "params": {
                "columns": "*",
                "filters": filters,
                "position": f"{ra}, {dec}, {radius}"
            }
        }
    else:
        # Fallback to name-based search using Mast.Caom.Filtered
        filters.append({"paramName": "target_name", "values": [], "freeText": f"%{target}%"})
        request_obj = {
            "service": "Mast.Caom.Filtered",
            "format": "json",
            "pagesize": max_results,
            "page": 1,
            "params": {
                "columns": "*",
                "filters": filters
            }
        }
        
    search_params = {
        "request": json.dumps(request_obj)
    }

    try:
        response = requests.post(
            search_url,
            data=search_params,
            headers={
                "Content-type": "application/x-www-form-urlencoded",
                "Accept": "text/plain",
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        raw_results = data.get("data", [])
        return raw_results
    except Exception as e:
        logger.error(f"MAST filtered query failed: {e}")
        return []


@mcp.tool()
def search_observations(
    target: str,
    mission: Optional[str] = None,
    limit: int = 15,
) -> str:
    """Search MAST for observations of a specific astronomical target.

    Args:
        target: Name of the target (e.g., 'TRAPPIST-1', 'Kepler-442', 'M31').
        mission: Optional filter by mission (e.g., 'JWST', 'HST', 'TESS', 'Kepler', 'K2').
                 Leave empty to search all missions.
        limit: Maximum number of results (default: 15).

    Returns:
        List of observations with mission, instrument, wavelength, and observation date.
    """
    results = _mast_filtered_query(target, mission=mission, max_results=limit)

    if not results:
        # Fallback: try a simple name search
        return _search_observations_fallback(target, mission, limit)

    output_lines = [f"## MAST Observations for '{target}'\n"]
    if mission:
        output_lines.append(f"Filtered by mission: **{mission}**\n")
    output_lines.append(f"Found {len(results)} observation(s):\n")

    for i, obs in enumerate(results, 1):
        obs_collection = obs.get("obs_collection", "Unknown")
        instrument = obs.get("instrument_name", "Unknown")
        obs_id = obs.get("obs_id", "N/A")
        target_name = obs.get("target_name", target)
        wavelength = obs.get("wavelength_region", "N/A")
        t_min = obs.get("t_min", None)
        t_max = obs.get("t_max", None)
        data_type = obs.get("dataproduct_type", "N/A")
        calib_level = obs.get("calib_level", "N/A")

        output_lines.append(f"### {i}. {obs_collection} — {instrument}")
        output_lines.append(f"- **Observation ID:** {obs_id}")
        output_lines.append(f"- **Target:** {target_name}")
        output_lines.append(f"- **Wavelength Region:** {wavelength}")
        output_lines.append(f"- **Data Type:** {data_type}")
        output_lines.append(f"- **Calibration Level:** {calib_level}")
        if t_min:
            output_lines.append(f"- **Observation Start (MJD):** {t_min}")
        
        jpeg_url = obs.get("jpegURL")
        if jpeg_url:
            if jpeg_url.startswith("mast:"):
                jpeg_url = f"https://mast.stsci.edu/api/v0.1/Download/file?uri={jpeg_url}"
            output_lines.append(f"- **Preview Image:** [View Preview]({jpeg_url})")
            
        output_lines.append("")

    output_lines.append(
        "*Data source: MAST (https://archive.stsci.edu)*"
    )
    return "\n".join(output_lines)


def _search_observations_fallback(
    target: str, mission: Optional[str], limit: int
) -> str:
    """Fallback search using a simpler API approach."""
    # Try using the count endpoint to at least confirm the target exists
    mission_filter = f" from {mission}" if mission else ""
    return (
        f"## MAST Observations for '{target}'\n\n"
        f"No observations found{mission_filter}. This could mean:\n"
        f"- The target name needs to be more specific\n"
        f"- No observations exist for this target in MAST\n\n"
        f"**Suggestions:**\n"
        f"- Try the exact catalog name (e.g., 'TRAPPIST-1' instead of 'Trappist')\n"
        f"- Use `get_available_missions` to see which missions observed this target\n"
        f"- Check https://mast.stsci.edu for manual search\n\n"
        f"*Data source: MAST (https://archive.stsci.edu)*"
    )


@mcp.tool()
def get_available_missions(target: str) -> str:
    """List which space missions have observed a specific target.

    Args:
        target: Name of the astronomical target.

    Returns:
        A list of missions with observation counts.
    """
    results = _mast_filtered_query(target, max_results=100)

    if not results:
        return f"No observations found for '{target}' in MAST."

    # Count by mission
    mission_counts: dict[str, int] = {}
    instruments: dict[str, set] = {}

    for obs in results:
        mission = obs.get("obs_collection", "Unknown")
        instrument = obs.get("instrument_name", "Unknown")
        mission_counts[mission] = mission_counts.get(mission, 0) + 1
        if mission not in instruments:
            instruments[mission] = set()
        instruments[mission].add(instrument)

    # Sort by count
    sorted_missions = sorted(mission_counts.items(), key=lambda x: x[1], reverse=True)

    output_lines = [f"## Missions That Observed '{target}'\n"]
    output_lines.append(
        f"Total: {len(results)} observations across {len(mission_counts)} mission(s)\n"
    )
    output_lines.append("| Mission | Observations | Instruments |")
    output_lines.append("|---------|-------------|-------------|")

    for mission, count in sorted_missions:
        inst_list = ", ".join(sorted(instruments.get(mission, {"?"})))
        output_lines.append(f"| {mission} | {count} | {inst_list} |")

    output_lines.append("")
    output_lines.append("*Data source: MAST (https://archive.stsci.edu)*")

    return "\n".join(output_lines)


@mcp.tool()
def get_observation_summary(target: str) -> str:
    """Get a high-level summary of all observations available for a target.

    Args:
        target: Name of the astronomical target.

    Returns:
        Summary including total observations, missions, wavelength coverage, and date range.
    """
    results = _mast_filtered_query(target, max_results=200)

    if not results:
        return f"No observations found for '{target}' in MAST."

    missions = set()
    instruments = set()
    wavelengths = set()
    data_types = set()
    dates = []

    for obs in results:
        missions.add(obs.get("obs_collection", "Unknown"))
        instruments.add(obs.get("instrument_name", "Unknown"))
        if obs.get("wavelength_region"):
            wavelengths.add(obs["wavelength_region"])
        if obs.get("dataproduct_type"):
            data_types.add(obs["dataproduct_type"])
        if obs.get("t_min"):
            dates.append(obs["t_min"])

    date_range = ""
    if dates:
        date_range = f"MJD {min(dates):.1f} to {max(dates):.1f}"

    # Find any observations with a preview image (prioritize JWST, then HST, then others)
    # ONLY select from actual 2D images (dataproduct_type == image) to avoid tiny placeholder icons for data cubes.
    preview_obs = None
    for obs in results:
        if obs.get("jpegURL") and obs.get("dataproduct_type", "").lower() == "image":
            mission = obs.get("obs_collection", "").upper()
            if "JWST" in mission:
                preview_obs = obs
                break  # JWST is top priority, stop immediately
            elif "HST" in mission or "HUBBLE" in mission:
                # Keep first HST/Hubble preview found, but continue looking for JWST
                if not preview_obs or "JWST" not in preview_obs.get("obs_collection", "").upper():
                    preview_obs = obs
            elif not preview_obs:
                preview_obs = obs

    preview_section = ""
    if preview_obs:
        raw_url = preview_obs.get('jpegURL')
        preview_url = f"https://mast.stsci.edu/api/v0.1/Download/file?uri={raw_url}" if raw_url and raw_url.startswith("mast:") else raw_url
        preview_section = f"""### High-Resolution Preview Image\n- **Mission:** {preview_obs.get('obs_collection', 'Unknown')}\n- **Instrument:** {preview_obs.get('instrument_name', 'Unknown')}\n- **Description:** {preview_obs.get('obs_title', 'Target observation field')}\n- **Preview URL:** {preview_url}\n"""

    output = f"## Observation Summary for '{target}'\n\n### Overview\n- **Total Observations:** {len(results)}\n- **Missions:** {', '.join(sorted(missions))}\n- **Instruments:** {', '.join(sorted(instruments))}\n- **Wavelength Coverage:** {', '.join(sorted(wavelengths))}\n- **Data Types:** {', '.join(sorted(data_types))}\n- **Date Range:** {date_range if date_range else 'N/A'}\n\n### Significance\nThis target has been observed by {len(missions)} mission(s) using {len(instruments)} instrument(s),\ncovering {len(wavelengths)} wavelength region(s). This indicates {'strong' if len(results) > 20 else 'moderate' if len(results) > 5 else 'limited'} scientific interest.\n{preview_section}\n*Data source: MAST (https://archive.stsci.edu)*\n"
    return output


# Entry point for running as standalone MCP server
if __name__ == "__main__":
    logger.info("Starting MAST Archive MCP Server...")
    mcp.run(transport="stdio")
