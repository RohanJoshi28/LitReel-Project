from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

import requests


@dataclass
class StockImage:
    id: str
    url: str
    thumbnail: str
    photographer: str


class StockImageService:
    def __init__(self, api_key: str | None, results_per_page: int = 12) -> None:
        self.api_key = api_key or ""
        self.results_per_page = results_per_page
        self.endpoint = "https://api.pexels.com/v1/search"

    def search(self, query: str) -> list[dict]:
        if not query:
            return []
        if not self.api_key:
            return [img.__dict__ for img in self._placeholder_results(query)]

        headers = {"Authorization": self.api_key}
        params = {"query": query, "per_page": self.results_per_page}
        try:
            response = requests.get(self.endpoint, headers=headers, params=params, timeout=15)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return [img.__dict__ for img in self._placeholder_results(query)]

        items = []
        for photo in payload.get("photos", []):
            src = photo.get("src", {})
            items.append(
                StockImage(
                    id=str(photo.get("id")),
                    url=src.get("large") or src.get("original"),
                    thumbnail=src.get("medium") or src.get("small"),
                    photographer=photo.get("photographer", "Unknown"),
                ).__dict__
            )
        if not items:
            return [img.__dict__ for img in self._placeholder_results(query)]
        return items

    def _placeholder_results(self, query: str) -> list[StockImage]:
        seed = int(hashlib.sha256(query.encode("utf-8")).hexdigest(), 16)
        random.seed(seed)
        results = []
        for idx in range(4):
            size = random.choice([400, 500, 600])
            url = f"https://picsum.photos/seed/{seed + idx}/{size}/{size}"
            results.append(
                StockImage(
                    id=f"placeholder-{idx}",
                    url=url,
                    thumbnail=url,
                    photographer="Placeholder",
                )
            )
        return results
