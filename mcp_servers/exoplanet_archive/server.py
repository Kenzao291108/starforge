"""
StarForge — NASA Exoplanet Archive MCP Server

Provides tools to query the NASA Exoplanet Archive for exoplanet
and stellar data using the Table Access Protocol (TAP).

Data source: https://exoplanetarchive.ipac.caltech.edu
API docs: https://exoplanetarchive.ipac.caltech.edu/docs/TAP/usingTAP.html
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
logger = logging.getLogger("exoplanet_archive_mcp")

# NASA Exoplanet Archive TAP endpoint
TAP_BASE_URL = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"

# Initialize MCP server
mcp = FastMCP(
    "NASA Exoplanet Archive",
    instructions="Query the NASA Exoplanet Archive for exoplanet and stellar data",
)

# Persistent session to reuse connection sockets and prevent SSL EOF errors
_session = None

def _get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": "StarForge/1.0 (Exoplanet Research Assistant)"})
    return _session


def _execute_tap_query(query: str, max_rows: int = 50) -> list[dict]:
    """Execute an ADQL query against the NASA Exoplanet Archive TAP service.

    Args:
        query: ADQL query string
        max_rows: Maximum number of rows to return

    Returns:
        List of dictionaries, each representing a row of results
    """
    import time

    data = {
        "query": query,
        "format": "json",
    }

    max_retries = 3
    retry_delay = 2.0

    for attempt in range(max_retries):
        try:
            logger.info(f"Executing TAP query (attempt {attempt + 1}/{max_retries}): {query[:100]}...")
            session = _get_session()
            response = session.post(TAP_BASE_URL, data=data, timeout=30)
            response.raise_for_status()
            result = response.json()

            # Handle empty results
            if not result:
                return []

            return result[:max_rows]

        except (requests.exceptions.RequestException, ValueError) as e:
            logger.warning(f"TAP query attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                # Sleep with backoff
                time.sleep(retry_delay * (attempt + 1))
            else:
                logger.error(f"TAP query failed after {max_retries} attempts: {e}")
                return [{"error": f"Query failed after {max_retries} attempts: {str(e)}"}]



@mcp.tool()
def search_planets(
    query: str,
    limit: int = 10,
) -> str:
    """Search for exoplanets by name, host star, or discovery method.

    Args:
        query: Search term — can be a planet name (e.g., 'TRAPPIST-1'),
               a star name, or a discovery method (e.g., 'Transit').
        limit: Maximum number of results to return (default: 10, max: 50).

    Returns:
        A formatted list of matching exoplanets with key parameters.
    """
    limit = min(limit, 50)

    # Build ADQL query — search across planet name and host star name
    adql = f"""
    SELECT TOP {limit}
        pl_name, hostname, discoverymethod, disc_year,
        pl_orbper, pl_rade, pl_bmasse, pl_eqt,
        sy_dist, pl_orbsmax
    FROM ps
    WHERE UPPER(pl_name) LIKE UPPER('%{query}%')
       OR UPPER(hostname) LIKE UPPER('%{query}%')
    ORDER BY disc_year DESC
    """

    results = _execute_tap_query(adql, max_rows=limit)

    if not results:
        return f"No exoplanets found matching '{query}'."

    if "error" in results[0]:
        return results[0]["error"]

    # Format results
    output_lines = [f"## Exoplanet Search Results for '{query}'\n"]
    output_lines.append(f"Found {len(results)} result(s):\n")

    for i, planet in enumerate(results, 1):
        name = planet.get("pl_name", "Unknown")
        host = planet.get("hostname", "Unknown")
        method = planet.get("discoverymethod", "Unknown")
        year = planet.get("disc_year", "N/A")
        period = planet.get("pl_orbper", None)
        radius = planet.get("pl_rade", None)
        mass = planet.get("pl_bmasse", None)
        temp = planet.get("pl_eqt", None)
        dist = planet.get("sy_dist", None)

        output_lines.append(f"### {i}. {name}")
        output_lines.append(f"- **Host Star:** {host}")
        output_lines.append(f"- **Discovery:** {method} ({year})")
        if period:
            output_lines.append(f"- **Orbital Period:** {period:.4f} days")
        if radius:
            output_lines.append(f"- **Radius:** {radius:.2f} Earth radii")
        if mass:
            output_lines.append(f"- **Mass:** {mass:.2f} Earth masses")
        if temp:
            output_lines.append(f"- **Equilibrium Temp:** {temp:.0f} K")
        if dist:
            output_lines.append(f"- **Distance:** {dist:.2f} parsecs")
        output_lines.append("")

    output_lines.append(
        "*Data source: NASA Exoplanet Archive (https://exoplanetarchive.ipac.caltech.edu)*"
    )
    return "\n".join(output_lines)


@mcp.tool()
def get_planet_parameters(planet_name: str) -> str:
    """Get detailed parameters for a specific exoplanet.

    Args:
        planet_name: The exact name of the exoplanet (e.g., 'TRAPPIST-1 e', 'Kepler-442 b').

    Returns:
        Comprehensive planetary parameters including orbital, physical, and system data.
    """
    adql = f"""
    SELECT
        pl_name, hostname, discoverymethod, disc_year, disc_facility,
        pl_orbper, pl_orbpererr1, pl_orbpererr2,
        pl_orbsmax, pl_orbsmaxerr1, pl_orbsmaxerr2,
        pl_rade, pl_radeerr1, pl_radeerr2,
        pl_bmasse, pl_bmasseerr1, pl_bmasseerr2,
        pl_orbeccen,
        pl_eqt, pl_eqterr1, pl_eqterr2,
        pl_orbincl,
        pl_tranmid,
        pl_trandep, pl_trandur,
        sy_dist, sy_vmag, sy_kmag,
        pl_dens,
        pl_insol, pl_insolerr1, pl_insolerr2,
        pl_ratdor, pl_ratror,
        rowupdate
    FROM ps
    WHERE UPPER(pl_name) = UPPER('{planet_name}')
    ORDER BY rowupdate DESC
    """

    results = _execute_tap_query(adql, max_rows=1)

    if not results:
        return f"No data found for planet '{planet_name}'. Try searching with search_planets first."

    if "error" in results[0]:
        return results[0]["error"]

    p = results[0]

    def fmt(val, err1=None, err2=None, unit="", decimals=4):
        """Format a value with optional error bars."""
        if val is None:
            return "N/A"
        result = f"{val:.{decimals}f}"
        if err1 is not None and err2 is not None:
            result += f" (+{abs(err1):.{decimals}f} / -{abs(err2):.{decimals}f})"
        if unit:
            result += f" {unit}"
        return result

    output = f"""## {p.get('pl_name', planet_name)} — Detailed Parameters

### Discovery
- **Method:** {p.get('discoverymethod', 'N/A')}
- **Year:** {p.get('disc_year', 'N/A')}
- **Facility:** {p.get('disc_facility', 'N/A')}

### Orbital Parameters
- **Orbital Period:** {fmt(p.get('pl_orbper'), p.get('pl_orbpererr1'), p.get('pl_orbpererr2'), 'days')}
- **Semi-major Axis:** {fmt(p.get('pl_orbsmax'), p.get('pl_orbsmaxerr1'), p.get('pl_orbsmaxerr2'), 'AU')}
- **Eccentricity:** {fmt(p.get('pl_orbeccen'), decimals=4)}
- **Inclination:** {fmt(p.get('pl_orbincl'), decimals=2, unit='°')}

### Physical Parameters
- **Radius:** {fmt(p.get('pl_rade'), p.get('pl_radeerr1'), p.get('pl_radeerr2'), 'Earth radii', 2)}
- **Mass:** {fmt(p.get('pl_bmasse'), p.get('pl_bmasseerr1'), p.get('pl_bmasseerr2'), 'Earth masses', 2)}
- **Density:** {fmt(p.get('pl_dens'), decimals=2, unit='g/cm³')}
- **Equilibrium Temp:** {fmt(p.get('pl_eqt'), p.get('pl_eqterr1'), p.get('pl_eqterr2'), 'K', 0)}
- **Insolation Flux:** {fmt(p.get('pl_insol'), p.get('pl_insolerr1'), p.get('pl_insolerr2'), 'Earth flux', 2)}

### Transit Parameters
- **Transit Depth:** {fmt(p.get('pl_trandep'), decimals=6, unit='%')}
- **Transit Duration:** {fmt(p.get('pl_trandur'), decimals=4, unit='hours')}
- **Transit Midpoint:** {fmt(p.get('pl_tranmid'), decimals=4, unit='BJD')}
- **Planet/Star Radius Ratio:** {fmt(p.get('pl_ratror'), decimals=6)}
- **Semi-major Axis / Star Radius:** {fmt(p.get('pl_ratdor'), decimals=2)}

### System Properties
- **Host Star:** {p.get('hostname', 'N/A')}
- **Distance:** {fmt(p.get('sy_dist'), decimals=2, unit='parsecs')}
- **V-band Magnitude:** {fmt(p.get('sy_vmag'), decimals=2)}
- **K-band Magnitude:** {fmt(p.get('sy_kmag'), decimals=2)}

### Metadata
- **Last Updated:** {p.get('rowupdate', 'N/A')}

*Data source: NASA Exoplanet Archive (https://exoplanetarchive.ipac.caltech.edu)*
"""
    return output


@mcp.tool()
def get_stellar_parameters(star_name: str) -> str:
    """Get detailed parameters for a host star of an exoplanet system.

    Args:
        star_name: The name of the host star (e.g., 'TRAPPIST-1', 'Kepler-442').

    Returns:
        Comprehensive stellar parameters including spectral type, temperature, and luminosity.
    """
    adql = f"""
    SELECT DISTINCT
        hostname,
        st_spectype, st_teff, st_tefferr1, st_tefferr2,
        st_rad, st_raderr1, st_raderr2,
        st_mass, st_masserr1, st_masserr2,
        st_lum, st_lumerr1, st_lumerr2,
        st_logg, st_loggerr1, st_loggerr2,
        st_met, st_meterr1, st_meterr2,
        st_age, st_ageerr1, st_ageerr2,
        st_rotp,
        sy_dist, sy_disterr1, sy_disterr2,
        sy_vmag, sy_kmag,
        sy_pnum
    FROM stellarhosts
    WHERE UPPER(hostname) = UPPER('{star_name}')
    """

    results = _execute_tap_query(adql, max_rows=1)

    if not results:
        return f"No stellar data found for '{star_name}'."

    if "error" in results[0]:
        return results[0]["error"]

    s = results[0]

    def fmt(val, err1=None, err2=None, unit="", decimals=4):
        if val is None:
            return "N/A"
        result = f"{val:.{decimals}f}"
        if err1 is not None and err2 is not None:
            result += f" (+{abs(err1):.{decimals}f} / -{abs(err2):.{decimals}f})"
        if unit:
            result += f" {unit}"
        return result

    output = f"""## {s.get('hostname', star_name)} — Stellar Parameters

### Fundamental Properties
- **Spectral Type:** {s.get('st_spectype', 'N/A')}
- **Effective Temperature:** {fmt(s.get('st_teff'), s.get('st_tefferr1'), s.get('st_tefferr2'), 'K', 0)}
- **Radius:** {fmt(s.get('st_rad'), s.get('st_raderr1'), s.get('st_raderr2'), 'Solar radii', 3)}
- **Mass:** {fmt(s.get('st_mass'), s.get('st_masserr1'), s.get('st_masserr2'), 'Solar masses', 3)}
- **Luminosity (log):** {fmt(s.get('st_lum'), s.get('st_lumerr1'), s.get('st_lumerr2'), 'log(Solar)', 3)}
- **Surface Gravity (log g):** {fmt(s.get('st_logg'), s.get('st_loggerr1'), s.get('st_loggerr2'), 'cgs', 3)}

### Composition & Age
- **Metallicity [Fe/H]:** {fmt(s.get('st_met'), s.get('st_meterr1'), s.get('st_meterr2'), 'dex', 3)}
- **Age:** {fmt(s.get('st_age'), s.get('st_ageerr1'), s.get('st_ageerr2'), 'Gyr', 2)}
- **Rotation Period:** {fmt(s.get('st_rotp'), decimals=2, unit='days')}

### System Properties
- **Distance:** {fmt(s.get('sy_dist'), s.get('sy_disterr1'), s.get('sy_disterr2'), 'parsecs', 2)}
- **V-band Magnitude:** {fmt(s.get('sy_vmag'), decimals=2)}
- **K-band Magnitude:** {fmt(s.get('sy_kmag'), decimals=2)}
- **Number of Known Planets:** {s.get('sy_pnum', 'N/A')}

*Data source: NASA Exoplanet Archive (https://exoplanetarchive.ipac.caltech.edu)*
"""
    return output


@mcp.tool()
def get_transit_data(planet_name: str) -> str:
    """Get transit-specific data for an exoplanet (useful for observation planning).

    Args:
        planet_name: The name of the transiting exoplanet.

    Returns:
        Transit parameters including depth, duration, ephemeris, and observation feasibility.
    """
    adql = f"""
    SELECT
        pl_name, hostname,
        pl_tranmid, pl_tranmiderr1, pl_tranmiderr2,
        pl_orbper, pl_orbpererr1, pl_orbpererr2,
        pl_trandep, pl_trandeperr1, pl_trandeperr2,
        pl_trandur, pl_trandurerr1, pl_trandurerr2,
        pl_ratdor, pl_ratror,
        pl_orbincl,
        pl_rade, pl_bmasse,
        st_teff, st_rad, sy_vmag,
        discoverymethod
    FROM ps
    WHERE UPPER(pl_name) = UPPER('{planet_name}')
       AND discoverymethod = 'Transit'
    ORDER BY rowupdate DESC
    """

    results = _execute_tap_query(adql, max_rows=1)

    if not results:
        # Try without transit filter — planet might exist but not be a transiting planet
        adql_fallback = f"""
        SELECT pl_name, discoverymethod
        FROM ps
        WHERE UPPER(pl_name) = UPPER('{planet_name}')
        """
        fallback = _execute_tap_query(adql_fallback, max_rows=1)
        if fallback and "error" not in fallback[0]:
            method = fallback[0].get("discoverymethod", "unknown method")
            return (
                f"'{planet_name}' was discovered via {method}, not transit. "
                f"Transit data is not available for this planet."
            )
        return f"No data found for '{planet_name}'."

    if "error" in results[0]:
        return results[0]["error"]

    t = results[0]

    def fmt(val, err1=None, err2=None, unit="", decimals=4):
        if val is None:
            return "N/A"
        result = f"{val:.{decimals}f}"
        if err1 is not None and err2 is not None:
            result += f" (+{abs(err1):.{decimals}f} / -{abs(err2):.{decimals}f})"
        if unit:
            result += f" {unit}"
        return result

    output = f"""## {t.get('pl_name', planet_name)} — Transit Data

### Transit Ephemeris
- **Transit Midpoint (T0):** {fmt(t.get('pl_tranmid'), t.get('pl_tranmiderr1'), t.get('pl_tranmiderr2'), 'BJD', 5)}
- **Orbital Period:** {fmt(t.get('pl_orbper'), t.get('pl_orbpererr1'), t.get('pl_orbpererr2'), 'days', 6)}

### Transit Geometry
- **Transit Depth:** {fmt(t.get('pl_trandep'), t.get('pl_trandeperr1'), t.get('pl_trandeperr2'), '%', 4)}
- **Transit Duration:** {fmt(t.get('pl_trandur'), t.get('pl_trandurerr1'), t.get('pl_trandurerr2'), 'hours', 4)}
- **Planet/Star Radius Ratio:** {fmt(t.get('pl_ratror'), decimals=6)}
- **Semi-major Axis / Star Radius:** {fmt(t.get('pl_ratdor'), decimals=2)}
- **Orbital Inclination:** {fmt(t.get('pl_orbincl'), decimals=2, unit='°')}

### System Context
- **Planet Radius:** {fmt(t.get('pl_rade'), decimals=2, unit='Earth radii')}
- **Planet Mass:** {fmt(t.get('pl_bmasse'), decimals=2, unit='Earth masses')}
- **Star Temperature:** {fmt(t.get('st_teff'), decimals=0, unit='K')}
- **Star Radius:** {fmt(t.get('st_rad'), decimals=3, unit='Solar radii')}
- **System V Magnitude:** {fmt(t.get('sy_vmag'), decimals=2)}

*Data source: NASA Exoplanet Archive (https://exoplanetarchive.ipac.caltech.edu)*
"""
    return output


@mcp.tool()
def list_habitable_zone_planets(limit: int = 20) -> str:
    """List potentially habitable exoplanets (those in the habitable zone).

    Selects planets with equilibrium temperatures roughly between 180K and 310K
    and with radii less than 2 Earth radii (likely rocky).

    Args:
        limit: Maximum number of results (default: 20, max: 50).

    Returns:
        A list of potentially habitable exoplanets with key parameters.
    """
    limit = min(limit, 50)

    adql = f"""
    SELECT TOP {limit}
        pl_name, hostname, discoverymethod, disc_year,
        pl_orbper, pl_rade, pl_bmasse, pl_eqt,
        pl_insol, sy_dist, pl_orbsmax
    FROM ps
    WHERE pl_eqt BETWEEN 180 AND 310
      AND pl_rade < 2.0
      AND pl_rade IS NOT NULL
      AND pl_eqt IS NOT NULL
    ORDER BY ABS(pl_eqt - 255) ASC
    """

    results = _execute_tap_query(adql, max_rows=limit)

    if not results:
        return "No habitable zone planets found matching criteria."

    if "error" in results[0]:
        return results[0]["error"]

    output_lines = ["## Potentially Habitable Exoplanets\n"]
    output_lines.append(
        "Criteria: Equilibrium temp 180-310K, radius < 2.0 Earth radii\n"
    )
    output_lines.append(
        "Sorted by proximity to Earth's equilibrium temperature (255K)\n"
    )

    # Table header
    output_lines.append(
        "| # | Planet | Host Star | Temp (K) | Radius (R⊕) | Mass (M⊕) | Period (days) | Distance (pc) |"
    )
    output_lines.append(
        "|---|--------|-----------|----------|-------------|-----------|---------------|---------------|"
    )

    for i, p in enumerate(results, 1):
        name = p.get("pl_name", "?")
        host = p.get("hostname", "?")
        temp = f"{p['pl_eqt']:.0f}" if p.get("pl_eqt") else "?"
        radius = f"{p['pl_rade']:.2f}" if p.get("pl_rade") else "?"
        mass = f"{p['pl_bmasse']:.1f}" if p.get("pl_bmasse") else "?"
        period = f"{p['pl_orbper']:.2f}" if p.get("pl_orbper") else "?"
        dist = f"{p['sy_dist']:.1f}" if p.get("sy_dist") else "?"

        output_lines.append(
            f"| {i} | {name} | {host} | {temp} | {radius} | {mass} | {period} | {dist} |"
        )

    output_lines.append("")
    output_lines.append(f"*Showing {len(results)} results.*")
    output_lines.append(
        "*Data source: NASA Exoplanet Archive (https://exoplanetarchive.ipac.caltech.edu)*"
    )

    return "\n".join(output_lines)


@mcp.tool()
def get_discovery_statistics() -> str:
    """Get summary statistics about exoplanet discoveries.

    Returns:
        Statistics including total confirmed planets, discoveries by method,
        discoveries by year, and other aggregate data.
    """
    # Total count
    total_query = "SELECT COUNT(*) as total FROM ps WHERE default_flag = 1"
    total_result = _execute_tap_query(total_query, max_rows=1)
    total = total_result[0].get("total", "?") if total_result else "?"

    # By discovery method
    method_query = """
    SELECT discoverymethod, COUNT(*) as count
    FROM ps
    WHERE default_flag = 1
    GROUP BY discoverymethod
    ORDER BY count DESC
    """
    method_results = _execute_tap_query(method_query, max_rows=20)

    # Recent discoveries (last 2 years)
    recent_query = """
    SELECT disc_year, COUNT(*) as count
    FROM ps
    WHERE default_flag = 1 AND disc_year >= 2024
    GROUP BY disc_year
    ORDER BY disc_year DESC
    """
    recent_results = _execute_tap_query(recent_query, max_rows=10)

    output = f"""## Exoplanet Discovery Statistics

### Total Confirmed Exoplanets: **{total}**

### Discoveries by Method
"""

    if method_results and "error" not in method_results[0]:
        output += "| Method | Count |\n|--------|-------|\n"
        for m in method_results:
            method = m.get("discoverymethod", "?")
            count = m.get("count", "?")
            output += f"| {method} | {count} |\n"

    output += "\n### Recent Discoveries\n"
    if recent_results and "error" not in recent_results[0]:
        output += "| Year | Count |\n|------|-------|\n"
        for r in recent_results:
            year = r.get("disc_year", "?")
            count = r.get("count", "?")
            output += f"| {year} | {count} |\n"

    output += "\n*Data source: NASA Exoplanet Archive (https://exoplanetarchive.ipac.caltech.edu)*"
    return output


# Entry point for running as standalone MCP server
if __name__ == "__main__":
    logger.info("Starting NASA Exoplanet Archive MCP Server...")
    mcp.run(transport="stdio")
