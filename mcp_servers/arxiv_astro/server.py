"""
StarForge — arXiv Astronomy MCP Server

Provides tools to search and retrieve scientific papers from arXiv,
specifically focused on astronomy and astrophysics categories.

Data source: https://arxiv.org
API docs: https://info.arxiv.org/help/api/
"""

import logging
import sys
import xml.etree.ElementTree as ET
from typing import Optional
from datetime import datetime, timedelta

import requests
from mcp.server.fastmcp import FastMCP

# Configure logging to stderr
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("arxiv_astro_mcp")

# arXiv API endpoint
ARXIV_API_URL = "http://export.arxiv.org/api/query"

# Astronomy arXiv categories
ASTRO_CATEGORIES = {
    "astro-ph.EP": "Earth and Planetary Astrophysics (exoplanets, solar system)",
    "astro-ph.SR": "Solar and Stellar Astrophysics",
    "astro-ph.GA": "Astrophysics of Galaxies",
    "astro-ph.CO": "Cosmology and Nongalactic Astrophysics",
    "astro-ph.HE": "High Energy Astrophysical Phenomena",
    "astro-ph.IM": "Instrumentation and Methods",
}

# Initialize MCP server
mcp = FastMCP(
    "arXiv Astronomy",
    instructions="Search and retrieve astronomy research papers from arXiv",
)

# XML namespace for Atom feed
ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def _parse_arxiv_response(xml_text: str) -> list[dict]:
    """Parse arXiv API Atom XML response into a list of paper dictionaries."""
    papers = []

    try:
        root = ET.fromstring(xml_text)

        for entry in root.findall(f"{ATOM_NS}entry"):
            paper = {}

            # Basic metadata
            paper["id"] = entry.findtext(f"{ATOM_NS}id", "").strip()
            paper["title"] = " ".join(
                entry.findtext(f"{ATOM_NS}title", "").split()
            )  # Normalize whitespace
            paper["summary"] = " ".join(
                entry.findtext(f"{ATOM_NS}summary", "").split()
            )
            paper["published"] = entry.findtext(f"{ATOM_NS}published", "")[:10]  # Date only
            paper["updated"] = entry.findtext(f"{ATOM_NS}updated", "")[:10]

            # Authors
            authors = []
            for author in entry.findall(f"{ATOM_NS}author"):
                name = author.findtext(f"{ATOM_NS}name", "Unknown")
                authors.append(name)
            paper["authors"] = authors

            # Categories
            categories = []
            for cat in entry.findall(f"{ATOM_NS}category"):
                term = cat.get("term", "")
                if term:
                    categories.append(term)
            paper["categories"] = categories

            # Links (PDF, abstract)
            for link in entry.findall(f"{ATOM_NS}link"):
                if link.get("title") == "pdf":
                    paper["pdf_url"] = link.get("href", "")
                elif link.get("type") == "text/html":
                    paper["abstract_url"] = link.get("href", "")

            # arXiv ID extraction
            arxiv_id = paper["id"].split("/abs/")[-1] if "/abs/" in paper["id"] else paper["id"]
            paper["arxiv_id"] = arxiv_id

            # Comment (often contains page count, journal reference)
            comment = entry.findtext(f"{ARXIV_NS}comment", "")
            paper["comment"] = comment

            # Journal reference
            journal_ref = entry.findtext(f"{ARXIV_NS}journal_ref", "")
            paper["journal_ref"] = journal_ref

            papers.append(paper)

    except ET.ParseError as e:
        logger.error(f"Failed to parse arXiv XML: {e}")

    return papers


def _query_arxiv(
    search_query: str,
    max_results: int = 10,
    sort_by: str = "relevance",
    sort_order: str = "descending",
) -> list[dict]:
    """Execute a query against the arXiv API.

    Args:
        search_query: arXiv search query string
        max_results: Maximum number of results
        sort_by: Sort criterion ('relevance', 'lastUpdatedDate', 'submittedDate')
        sort_order: 'ascending' or 'descending'

    Returns:
        List of paper dictionaries
    """
    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": max_results,
        "sortBy": sort_by,
        "sortOrder": sort_order,
    }

    try:
        logger.info(f"Querying arXiv: {search_query[:80]}...")
        response = requests.get(ARXIV_API_URL, params=params, timeout=30)
        response.raise_for_status()
        return _parse_arxiv_response(response.text)
    except requests.exceptions.RequestException as e:
        logger.error(f"arXiv query failed: {e}")
        return []


@mcp.tool()
def search_papers(
    query: str,
    category: str = "astro-ph.EP",
    max_results: int = 10,
    sort_by: str = "relevance",
) -> str:
    """Search for astronomy research papers on arXiv.

    Args:
        query: Search terms (e.g., 'TRAPPIST-1 atmosphere', 'exoplanet habitability',
               'transit spectroscopy JWST').
        category: arXiv category to search in. Options:
                  'astro-ph.EP' (exoplanets, default),
                  'astro-ph.SR' (stellar),
                  'astro-ph.GA' (galaxies),
                  'astro-ph.CO' (cosmology),
                  'astro-ph.HE' (high energy),
                  'astro-ph.IM' (instrumentation),
                  'all' (search all categories).
        max_results: Maximum number of papers to return (default: 10, max: 30).
        sort_by: How to sort results — 'relevance' (default) or 'submittedDate'.

    Returns:
        List of matching papers with titles, authors, dates, and abstracts.
    """
    max_results = min(max_results, 30)

    # Build search query
    if category and category != "all":
        search_query = f"cat:{category} AND all:{query}"
    else:
        search_query = f"all:{query}"

    papers = _query_arxiv(
        search_query,
        max_results=max_results,
        sort_by=sort_by,
        sort_order="descending",
    )

    if not papers:
        return f"No papers found for '{query}' in category '{category}'."

    cat_desc = ASTRO_CATEGORIES.get(category, category)

    output_lines = [f"## arXiv Search Results: '{query}'\n"]
    output_lines.append(f"**Category:** {category} — {cat_desc}")
    output_lines.append(f"**Results:** {len(papers)} paper(s)\n")

    for i, paper in enumerate(papers, 1):
        authors_str = ", ".join(paper["authors"][:5])
        if len(paper["authors"]) > 5:
            authors_str += f" et al. ({len(paper['authors'])} authors)"

        output_lines.append(f"### {i}. {paper['title']}")
        output_lines.append(f"- **Authors:** {authors_str}")
        output_lines.append(f"- **Published:** {paper['published']}")
        output_lines.append(f"- **arXiv ID:** {paper['arxiv_id']}")

        if paper.get("journal_ref"):
            output_lines.append(f"- **Journal:** {paper['journal_ref']}")

        # Truncate abstract to first 300 chars
        abstract = paper["summary"]
        if len(abstract) > 300:
            abstract = abstract[:297] + "..."
        output_lines.append(f"- **Abstract:** {abstract}")

        if paper.get("pdf_url"):
            output_lines.append(f"- **PDF:** {paper['pdf_url']}")

        output_lines.append("")

    output_lines.append("*Data source: arXiv (https://arxiv.org)*")
    return "\n".join(output_lines)


@mcp.tool()
def get_paper_abstract(arxiv_id: str) -> str:
    """Get the full abstract and details of a specific arXiv paper.

    Args:
        arxiv_id: The arXiv paper ID (e.g., '2310.12345' or '2310.12345v2').

    Returns:
        Full paper metadata including complete abstract, all authors, and links.
    """
    # Clean up ID
    arxiv_id = arxiv_id.strip()
    if arxiv_id.startswith("http"):
        arxiv_id = arxiv_id.split("/abs/")[-1].split("/pdf/")[-1]

    search_query = f"id:{arxiv_id}"
    papers = _query_arxiv(search_query, max_results=1)

    if not papers:
        return f"No paper found with arXiv ID '{arxiv_id}'."

    paper = papers[0]

    authors_str = "\n".join(f"  - {a}" for a in paper["authors"])

    output = f"""## {paper['title']}

### Metadata
- **arXiv ID:** {paper['arxiv_id']}
- **Published:** {paper['published']}
- **Updated:** {paper['updated']}
- **Categories:** {', '.join(paper['categories'])}

### Authors
{authors_str}

### Abstract
{paper['summary']}

### Links
- **Abstract Page:** {paper.get('abstract_url', paper['id'])}
- **PDF:** {paper.get('pdf_url', 'N/A')}

"""
    if paper.get("journal_ref"):
        output += f"### Journal Reference\n{paper['journal_ref']}\n\n"

    if paper.get("comment"):
        output += f"### Comments\n{paper['comment']}\n\n"

    output += "*Data source: arXiv (https://arxiv.org)*"
    return output


@mcp.tool()
def find_related_papers(
    arxiv_id: str,
    max_results: int = 5,
) -> str:
    """Find papers related to a specific arXiv paper by searching for its key topics.

    Args:
        arxiv_id: The arXiv ID of the paper to find related work for.
        max_results: Maximum number of related papers (default: 5).

    Returns:
        List of related papers based on the original paper's keywords and topic.
    """
    # First, get the original paper
    search_query = f"id:{arxiv_id.strip()}"
    original = _query_arxiv(search_query, max_results=1)

    if not original:
        return f"No paper found with arXiv ID '{arxiv_id}'."

    paper = original[0]

    # Extract key terms from the title for the related search
    title_words = paper["title"].split()
    # Filter out common words and keep important terms
    stop_words = {
        "the", "a", "an", "of", "in", "for", "and", "or", "with", "on",
        "to", "from", "by", "its", "at", "is", "are", "was", "were",
        "be", "been", "being", "have", "has", "had", "do", "does",
        "new", "using", "based", "toward", "towards",
    }
    key_terms = [w for w in title_words if w.lower() not in stop_words and len(w) > 2]
    search_terms = " ".join(key_terms[:6])  # Use top 6 terms

    # Get the primary category
    primary_cat = paper["categories"][0] if paper["categories"] else "astro-ph"

    # Search for related papers
    related_query = f"cat:{primary_cat} AND all:{search_terms}"
    related = _query_arxiv(
        related_query,
        max_results=max_results + 1,  # +1 to account for the original paper
        sort_by="relevance",
    )

    # Filter out the original paper
    related = [p for p in related if p["arxiv_id"] != paper["arxiv_id"]][:max_results]

    if not related:
        return f"No related papers found for '{paper['title']}'."

    output_lines = [f"## Papers Related to: {paper['title']}\n"]
    output_lines.append(f"**Original Paper:** {paper['arxiv_id']} ({paper['published']})")
    output_lines.append(f"**Search Terms:** {search_terms}\n")

    for i, rel in enumerate(related, 1):
        authors_short = ", ".join(rel["authors"][:3])
        if len(rel["authors"]) > 3:
            authors_short += " et al."

        abstract = rel["summary"]
        if len(abstract) > 200:
            abstract = abstract[:197] + "..."

        output_lines.append(f"### {i}. {rel['title']}")
        output_lines.append(f"- **Authors:** {authors_short}")
        output_lines.append(f"- **Published:** {rel['published']}")
        output_lines.append(f"- **arXiv ID:** {rel['arxiv_id']}")
        output_lines.append(f"- **Abstract:** {abstract}")
        output_lines.append("")

    output_lines.append("*Data source: arXiv (https://arxiv.org)*")
    return "\n".join(output_lines)


@mcp.tool()
def search_recent_papers(
    topic: str,
    days: int = 30,
    max_results: int = 10,
) -> str:
    """Search for recent astronomy papers on a specific topic.

    Args:
        topic: The research topic (e.g., 'exoplanet atmosphere', 'JWST spectroscopy').
        days: How many days back to search (default: 30, max: 365).
        max_results: Maximum number of papers (default: 10).

    Returns:
        List of recent papers sorted by submission date.
    """
    days = min(days, 365)
    max_results = min(max_results, 30)

    # Search with date sorting
    search_query = f"cat:astro-ph* AND all:{topic}"
    papers = _query_arxiv(
        search_query,
        max_results=max_results,
        sort_by="submittedDate",
        sort_order="descending",
    )

    if not papers:
        return f"No recent papers found for '{topic}'."

    # Filter by date (approximate — arXiv API doesn't support date ranges directly)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    recent_papers = [p for p in papers if p.get("published", "") >= cutoff]

    if not recent_papers:
        recent_papers = papers  # Fall back to all results if none match date filter

    output_lines = [f"## Recent Papers: '{topic}'\n"]
    output_lines.append(f"**Time Range:** Last {days} days")
    output_lines.append(f"**Results:** {len(recent_papers)} paper(s)\n")

    for i, paper in enumerate(recent_papers, 1):
        authors_short = ", ".join(paper["authors"][:3])
        if len(paper["authors"]) > 3:
            authors_short += " et al."

        abstract = paper["summary"]
        if len(abstract) > 250:
            abstract = abstract[:247] + "..."

        output_lines.append(f"### {i}. {paper['title']}")
        output_lines.append(f"- **Authors:** {authors_short}")
        output_lines.append(f"- **Published:** {paper['published']}")
        output_lines.append(f"- **arXiv ID:** {paper['arxiv_id']}")
        output_lines.append(f"- **Abstract:** {abstract}")
        output_lines.append("")

    output_lines.append("*Data source: arXiv (https://arxiv.org)*")
    return "\n".join(output_lines)


# Entry point
if __name__ == "__main__":
    logger.info("Starting arXiv Astronomy MCP Server...")
    mcp.run(transport="stdio")
