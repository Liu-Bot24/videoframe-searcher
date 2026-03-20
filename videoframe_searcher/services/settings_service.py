from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT_DIR / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

DEFAULT_SETTINGS: dict[str, Any] = {
    "workspace_root": str(ROOT_DIR / "workspace"),
    "use_cookie_auth": False,
    "cookie_browser": "chrome",
    "cookie_file": "",
    "http_proxy": "",
    "use_impersonate": True,
    "download_format": "bv*+ba/b",
    "merge_output_format": "mp4",
    "extra_yt_dlp_args": "",
}


class SettingsService:
    def __init__(self, settings_file: Path = SETTINGS_FILE) -> None:
        self.settings_file = settings_file

    def load(self) -> dict[str, Any]:
        if not self.settings_file.exists():
            self.save(DEFAULT_SETTINGS)
            return dict(DEFAULT_SETTINGS)

        with self.settings_file.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(loaded)
        return merged

    def save(self, settings: dict[str, Any]) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with self.settings_file.open("w", encoding="utf-8") as file:
            json.dump(settings, file, ensure_ascii=False, indent=2)
