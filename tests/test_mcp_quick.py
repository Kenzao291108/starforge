"""
Quick test for StarForge MCP servers.
Tests each MCP server's core functions directly (without MCP protocol).
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_exoplanet_archive():
    """Test NASA Exoplanet Archive MCP server tools."""
    print("=" * 60)
    print("Testing: NASA Exoplanet Archive MCP Server")
    print("=" * 60)

    from mcp_servers.exoplanet_archive.server import (
        search_planets,
        get_planet_parameters,
        get_stellar_parameters,
        get_discovery_statistics,
        list_habitable_zone_planets,
    )

    # Test 1: Search for planets
    print("\n--- Test 1: search_planets('TRAPPIST') ---")
    result = search_planets("TRAPPIST", limit=3)
    print(result[:500])
    assert "TRAPPIST" in result, "Should find TRAPPIST planets"
    print("✅ PASSED\n")

    # Test 2: Get planet parameters
    print("--- Test 2: get_planet_parameters('TRAPPIST-1 e') ---")
    result = get_planet_parameters("TRAPPIST-1 e")
    print(result[:500])
    assert "TRAPPIST-1 e" in result or "Detailed Parameters" in result, "Should return planet data"
    print("✅ PASSED\n")

    # Test 3: Get stellar parameters
    print("--- Test 3: get_stellar_parameters('TRAPPIST-1') ---")
    result = get_stellar_parameters("TRAPPIST-1")
    print(result[:500])
    assert "Stellar Parameters" in result or "TRAPPIST" in result, "Should return stellar data"
    print("✅ PASSED\n")

    # Test 4: Discovery statistics
    print("--- Test 4: get_discovery_statistics() ---")
    result = get_discovery_statistics()
    print(result[:500])
    assert "Discovery Statistics" in result, "Should return statistics"
    print("✅ PASSED\n")

    # Test 5: Habitable zone planets
    print("--- Test 5: list_habitable_zone_planets(limit=5) ---")
    result = list_habitable_zone_planets(limit=5)
    print(result[:500])
    assert "Habitable" in result, "Should return habitable zone planets"
    print("✅ PASSED\n")


def test_arxiv():
    """Test arXiv Astronomy MCP server tools."""
    print("=" * 60)
    print("Testing: arXiv Astronomy MCP Server")
    print("=" * 60)

    from mcp_servers.arxiv_astro.server import (
        search_papers,
        search_recent_papers,
    )

    # Test 1: Search papers
    print("\n--- Test 1: search_papers('TRAPPIST-1 atmosphere') ---")
    result = search_papers("TRAPPIST-1 atmosphere", max_results=3)
    print(result[:500])
    assert "arXiv" in result, "Should find arXiv papers"
    print("✅ PASSED\n")

    # Test 2: Recent papers
    print("--- Test 2: search_recent_papers('exoplanet JWST') ---")
    result = search_recent_papers("exoplanet JWST", days=90, max_results=3)
    print(result[:500])
    assert "Recent" in result or "arXiv" in result, "Should find recent papers"
    print("✅ PASSED\n")


def test_skyview():
    """Test SkyView MCP server tools."""
    print("=" * 60)
    print("Testing: NASA SkyView MCP Server")
    print("=" * 60)

    from mcp_servers.skyview.server import (
        get_sky_image,
        list_available_surveys,
    )

    # Test 1: List surveys
    print("\n--- Test 1: list_available_surveys() ---")
    result = list_available_surveys()
    print(result[:500])
    assert "DSS" in result, "Should list DSS survey"
    print("✅ PASSED\n")

    # Test 2: Get sky image
    print("--- Test 2: get_sky_image('M31', 'DSS') ---")
    result = get_sky_image("M31", survey="DSS", size_arcmin=30.0)
    print(result[:500])
    assert "Sky Image" in result or "SkyView" in result, "Should return image info"
    print("✅ PASSED\n")


if __name__ == "__main__":
    print("🔭 StarForge MCP Server Tests\n")

    try:
        test_exoplanet_archive()
        print("🎉 Exoplanet Archive: ALL TESTS PASSED\n")
    except Exception as e:
        print(f"❌ Exoplanet Archive FAILED: {e}\n")

    try:
        test_arxiv()
        print("🎉 arXiv: ALL TESTS PASSED\n")
    except Exception as e:
        print(f"❌ arXiv FAILED: {e}\n")

    try:
        test_skyview()
        print("🎉 SkyView: ALL TESTS PASSED\n")
    except Exception as e:
        print(f"❌ SkyView FAILED: {e}\n")

    print("=" * 60)
    print("🔭 All MCP server tests complete!")
    print("=" * 60)
