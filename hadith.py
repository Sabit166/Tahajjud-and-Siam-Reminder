"""HadithAPI client and prayer-time hadith formatting."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

import httpx

from config import HADITH_API_KEY, log

HADITH_API_BASE = "https://hadithapi.com/api"
ALLOWED_BOOKS = ("sahih-bukhari", "sahih-muslim")
REQUIRED_STATUS = "sahih"


@dataclass(frozen=True)
class Hadith:
    text: str
    book: str
    reference: str


_last_successful_hadith: Hadith | None = None


def _is_allowed_book(value: object) -> bool:
    normalized = str(value).lower().replace("-", " ").replace("_", " ")
    return "bukhari" in normalized or "muslim" in normalized


async def fetch_hadith(
    client: httpx.AsyncClient | None = None,
) -> Hadith:
    """Fetch one Sahih hadith from either Sahih Bukhari or Sahih Muslim."""
    global _last_successful_hadith

    if not HADITH_API_KEY:
        raise RuntimeError("HADITH_API_KEY is not configured.")

    own_client = client is None
    client = client or httpx.AsyncClient(timeout=20.0)
    try:
        book = secrets.choice(ALLOWED_BOOKS)
        response = await client.get(
            f"{HADITH_API_BASE}/hadiths",
            params={
                "apiKey": HADITH_API_KEY,
                "book": book,
                "status": REQUIRED_STATUS,
                "paginate": 100,
            },
        )
        response.raise_for_status()
        payload = response.json()
        candidates = [
            item
            for item in payload.get("hadiths", {}).get("data", [])
            if str(item.get("status", "")).lower() == REQUIRED_STATUS
            and _is_allowed_book(item.get("book", book))
            and item.get("hadithEnglish")
        ]
        if not candidates:
            raise ValueError(f"HadithAPI returned no qualifying hadiths for {book}.")

        item = secrets.choice(candidates)
        hadith = Hadith(
            text=str(item["hadithEnglish"]).strip(),
            book=str(item.get("book", book)).strip(),
            reference=str(item.get("hadithNumber", "")).strip(),
        )
        _last_successful_hadith = hadith
        return hadith
    except Exception:
        if _last_successful_hadith is not None:
            log.exception("HadithAPI fetch failed; reusing the last successful hadith.")
            return _last_successful_hadith
        raise
    finally:
        if own_client:
            await client.aclose()


def format_hadith_message(prayer_name: str, hadith: Hadith) -> str:
    """Build the English-only prayer-time hadith message."""
    nice_prayer = prayer_name.capitalize()
    reference = f" — {hadith.reference}" if hadith.reference else ""
    parts = [
        f"🕌 {nice_prayer} — Hadith of the Prayer",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        hadith.text,
        "",
        f"📚 {hadith.book.title()}{reference}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "May Allah grant us beneficial knowledge and righteous action. Ameen.",
    ]
    return "\n".join(parts)[:4000]
