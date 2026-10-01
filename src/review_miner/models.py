"""Store-agnostic data models so App Store and Steam look the same to the model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

Store = Literal["appstore", "steam"]


@dataclass
class App:
    store: Store
    app_id: str
    name: str
    developer: str = ""
    category: str = ""
    price: str = ""
    rank: int | None = None
    rating: float | None = None        # App Store: 1-5 average. Steam: % positive.
    rating_count: int | None = None
    version: str | None = None
    updated: str | None = None
    review_summary: str | None = None  # Steam: "Mostly Positive" etc.

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, "")}


@dataclass
class Review:
    store: Store
    app_id: str
    text: str
    rating: int | None = None          # App Store stars 1-5
    recommended: bool | None = None    # Steam thumbs up/down
    title: str = ""
    version: str | None = None
    date: str | None = None
    helpful_votes: int = 0
    playtime_hours: float | None = None
    review_id: str = ""                # store's own ID, used for de-duplication only

    @property
    def is_negative(self) -> bool:
        if self.rating is not None:
            return self.rating <= 2
        return self.recommended is False

    def to_dict(self, max_chars: int = 400) -> dict[str, Any]:
        d = {k: v for k, v in asdict(self).items()
             if k != "review_id" and not (v is None or v == "" or (v == 0 and not isinstance(v, bool)))}
        if len(self.text) > max_chars:
            d["text"] = self.text[:max_chars].rstrip() + "…"
        return d
