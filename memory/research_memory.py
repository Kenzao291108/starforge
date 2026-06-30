import os
import json
from datetime import datetime
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Any, Optional

@dataclass
class HistoryEntry:
    query: str
    timestamp: str
    brief_snippet: str
    target_name: Optional[str] = None

@dataclass
class WatchlistEntry:
    target_name: str
    timestamp: str
    notes: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)

@dataclass
class UserPreferences:
    detail_level: str = "comprehensive"  # "comprehensive", "compact", "stellar-only"
    favorite_categories: List[str] = field(default_factory=lambda: ["astro-ph.EP"])
    default_survey: str = "DSS"
    skyview_color_lut: str = "gray"

class ResearchMemoryManager:
    """Manages persistent exoplanet research history, watchlist, and preferences."""
    
    def __init__(self, storage_dir: Optional[str] = None):
        if storage_dir is None:
            # Default to ~/.starforge/memory
            self.storage_dir = os.path.expanduser("~/.starforge/memory")
        else:
            self.storage_dir = storage_dir
            
        os.makedirs(self.storage_dir, exist_ok=True)
        
        self.history_file = os.path.join(self.storage_dir, "history.json")
        self.watchlist_file = os.path.join(self.storage_dir, "watchlist.json")
        self.preferences_file = os.path.join(self.storage_dir, "preferences.json")
        
        # Load or initialize
        self.history: List[Dict[str, Any]] = self._load_json(self.history_file, [])
        self.watchlist: List[Dict[str, Any]] = self._load_json(self.watchlist_file, [])
        self.preferences: Dict[str, Any] = self._load_json(
            self.preferences_file, asdict(UserPreferences())
        )

    def _load_json(self, file_path: str, default: Any) -> Any:
        if not os.path.exists(file_path):
            return default
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    def _save_json(self, file_path: str, data: Any) -> None:
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving to {file_path}: {e}")

    def add_to_history(self, query: str, brief: str, target_name: Optional[str] = None) -> None:
        """Record a research brief query in the history log."""
        # Truncate brief snippet for storage size
        snippet = brief[:300] + "..." if len(brief) > 300 else brief
        entry = HistoryEntry(
            query=query,
            timestamp=datetime.now().isoformat(),
            brief_snippet=snippet,
            target_name=target_name
        )
        self.history.insert(0, asdict(entry))  # Most recent first
        # Limit history to top 50 entries
        self.history = self.history[:50]
        self._save_json(self.history_file, self.history)

    def get_history(self) -> List[Dict[str, Any]]:
        """Retrieve the exoplanet research history log."""
        return self.history

    def add_to_watchlist(self, target_name: str, notes: str = "", parameters: Optional[Dict[str, Any]] = None) -> None:
        """Add a planet or stellar system to the tracking watchlist."""
        # Check if already in watchlist and update it
        self.remove_from_watchlist(target_name)
        
        entry = WatchlistEntry(
            target_name=target_name,
            timestamp=datetime.now().isoformat(),
            notes=notes,
            parameters=parameters or {}
        )
        self.watchlist.insert(0, asdict(entry))
        self._save_json(self.watchlist_file, self.watchlist)

    def remove_from_watchlist(self, target_name: str) -> bool:
        """Remove a target from the watchlist."""
        initial_len = len(self.watchlist)
        self.watchlist = [entry for entry in self.watchlist if entry["target_name"].lower() != target_name.lower()]
        if len(self.watchlist) < initial_len:
            self._save_json(self.watchlist_file, self.watchlist)
            return True
        return False

    def get_watchlist(self) -> List[Dict[str, Any]]:
        """Retrieve all exoplanets/stellar systems on the watchlist."""
        return self.watchlist

    def update_preferences(self, detail_level: Optional[str] = None, favorite_categories: Optional[List[str]] = None, default_survey: Optional[str] = None, skyview_color_lut: Optional[str] = None, skyview_fov_arcmin: Optional[float] = None) -> None:
        """Update research preferences."""
        if detail_level:
            self.preferences["detail_level"] = detail_level
        if favorite_categories:
            self.preferences["favorite_categories"] = favorite_categories
        if default_survey:
            self.preferences["default_survey"] = default_survey
        if skyview_color_lut:
            self.preferences["skyview_color_lut"] = skyview_color_lut
        if skyview_fov_arcmin is not None:
            self.preferences["skyview_fov_arcmin"] = skyview_fov_arcmin
        self._save_json(self.preferences_file, self.preferences)

    def get_preferences(self) -> Dict[str, Any]:
        """Get the current user preferences."""
        # Ensure default color lut exists
        if "skyview_color_lut" not in self.preferences:
            self.preferences["skyview_color_lut"] = "gray"
        return self.preferences
