"""Unauthenticated YouTube Music search boundary."""

from .models import Candidate


class MusicSearch:
    def __init__(self) -> None:
        from ytmusicapi import YTMusic

        self._client = YTMusic()

    def songs(self, query: str) -> list[Candidate]:
        raw = self._client.search(query, filter="songs", limit=10)
        found: list[Candidate] = []
        seen: set[str] = set()
        for item in raw:
            video_id = item.get("videoId")
            if not video_id or video_id in seen or item.get("resultType") not in (None, "song"):
                continue
            seen.add(video_id)
            found.append(Candidate(
                video_id=video_id,
                title=item.get("title", ""),
                artists=[a.get("name", "") for a in item.get("artists", []) if a.get("name")],
                album=(item.get("album") or {}).get("name", ""),
                duration=item.get("duration") or "",
            ))
        return found
