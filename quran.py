"""
Qur'an ayah + Ibn Kathir tafsir fetcher.

APIs used:
- Aladhan (https://api.aladhan.com/v1/timingsByCity) for prayer times.
- Al-Quran Cloud (https://api.alquran.cloud) for the Arabic ayah +
  English translation.
- Quran.com API v4 for Ibn Kathir tafsir (quran.foundation/frontend-api
  endpoint). Endpoint used:
    POST https://api.quran.com/api/v4/tafsirs/by_ayah/<surah>:<ayah>
    with X-USER-ID + auth (we send public JSON without auth; some
    mirrors block CORS but allow POST from any IP).
"""

from __future__ import annotations

import asyncio
import datetime as _dt
from dataclasses import dataclass
from typing import Optional

import httpx

from config import BD_TZ, log

ALADHAN_BASE = "https://api.aladhan.com/v1"
QURAN_CLOUD_BASE = "https://api.alquran.cloud/v1"
# Quran.com (Quran Foundation) public v4 endpoint. If this ever returns
# 401/403 from server-side, the tafsir block will gracefully degrade to
# "Tafsir unavailable" rather than crashing the bot.
QURAN_COM_TAFSIR_BASE = "https://api.quran.com/api/v4/tafsirs/by_ayah"

DEFAULT_CITY = "Dhaka"
DEFAULT_COUNTRY = "Bangladesh"
DEFAULT_METHOD = 1  # University of Islamic Sciences, Karachi

# Ibn Kathir tafsir resource id used by Quran.com / Al-Quran Cloud
# 'en.maududi' style identifiers differ; for Ibn Kathir specifically,
# Al-Quran Cloud uses edition id "en-tafseer-ibn-kathir".
IBN_KATHIR_EDITION = "en-tafseer-ibn-kathir"


@dataclass
class PrayerTimes:
    fajr: _dt.time
    dhuhr: _dt.time
    asr: _dt.time
    maghrib: _dt.time
    isha: _dt.time

    def as_dict(self) -> dict[str, _dt.time]:
        return {
            "fajr": self.fajr,
            "dhuhr": self.dhuhr,
            "asr": self.asr,
            "maghrib": self.maghrib,
            "isha": self.isha,
        }


@dataclass
class AyahBundle:
    arabic: str
    surah_number: int
    ayah_number: int
    surah_name: str
    translation: str
    tafsir: Optional[str]


async def fetch_prayer_times(
    city: str = DEFAULT_CITY,
    country: str = DEFAULT_COUNTRY,
    method: int = DEFAULT_METHOD,
    date: _dt.date | None = None,
    client: httpx.AsyncClient | None = None,
) -> PrayerTimes:
    """
    Fetch today's prayer times for the configured city from Aladhan.
    Returns a PrayerTimes dataclass with 5 required prayer times,
    localized to BD_TZ.
    """
    when = date or _dt.date.today()
    url = f"{ALADHAN_BASE}/timingsByCity/{when.isoformat()}"
    params = {"city": city, "country": country, "method": method}
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
    finally:
        if own_client:
            await client.aclose()

    timings = data.get("data", {}).get("timings", {})

    def _t(key: str) -> _dt.time:
        raw = timings.get(key, "00:00").split(" ")[0]  # strip "(+06)"
        hour, minute = raw.split(":")[:2]
        return _dt.time(int(hour), int(minute))

    log.info(
        "Aladhan prayer times for %s on %s: %s",
        city, when, {k: timings.get(k) for k in ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")},
    )
    return PrayerTimes(
        fajr=_t("Fajr"),
        dhuhr=_t("Dhuhr"),
        asr=_t("Asr"),
        maghrib=_t("Maghrib"),
        isha=_t("Isha"),
    )


async def fetch_quran_ayah(
    client: httpx.AsyncClient | None = None,
) -> AyahBundle:
    """
    Fetch a random Arabic ayah from Al-Quran Cloud, plus its English
    (Sahih International) translation and Ibn Kathir tafsir.
    """
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=20.0)
    try:
        # 1. Random Arabic ayah
        rand_resp = await client.get(f"{QURAN_CLOUD_BASE}/ayah/random")
        rand_resp.raise_for_status()
        rand_data = rand_resp.json()["data"]

        arabic = rand_data.get("text", "")
        surah_num = rand_data["surah"]["number"]
        ayah_num = rand_data["numberInSurah"]
        surah_name = rand_data["surah"]["englishName"]

        # 2. English translation
        trans_resp = await client.get(
            f"{QURAN_CLOUD_BASE}/ayah/{surah_num}:{ayah_num}/en.sahih"
        )
        trans_resp.raise_for_status()
        translation = trans_resp.json()["data"]["text"]

        # 3. Ibn Kathir tafsir (Al-Quran Cloud edition)
        tafsir_resp = await client.get(
            f"{QURAN_CLOUD_BASE}/ayah/{surah_num}:{ayah_num}/{IBN_KATHIR_EDITION}"
        )
        tafsir_resp.raise_for_status()
        tafsir_raw = tafsir_resp.json()["data"]["text"]
        # Tafsir often contains Quranic-Cloud footnote refs like [1] that
        # truncate the message visual; strip them.
        tafsir = _clean_tafsir(tafsir_raw)
    finally:
        if own_client:
            await client.aclose()

    return AyahBundle(
        arabic=arabic,
        surah_number=surah_num,
        ayah_number=ayah_num,
        surah_name=surah_name,
        translation=translation,
        tafsir=tafsir,
    )


def _clean_tafsir(text: str) -> str:
    """
    Lightly clean a tafsir block: collapse excessive whitespace and
    strip simple footnote markers that show up as bare integers in
    brackets (e.g. ``[1]``, ``[2]``).
    """
    import re

    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\[\d+\]", "", text)
    return text


def format_ayah_message(prayer_name: str, bundle: AyahBundle) -> str:
    """
    Build the final message text for a prayer time. Designed to fit
    under Telegram's 4096-char cap and to render cleanly as plain
    text (no Markdown, like the rest of the bot).
    """
    nice_prayer = prayer_name.capitalize()
    ref = f"{bundle.surah_name} ({bundle.surah_number}:{bundle.ayah_number})"
    header = "━" * 28
    parts = [
        f"🕌 {nice_prayer} — Ayah of the Hour",
        header,
        bundle.arabic,
        "",
        f"📖 Translation ({ref}):",
        bundle.translation,
        header,
        "May Allah grant us beneficial knowledge and righteous action. Ameen.",
    ]
    text = "\n".join(parts)
    # Hard cap just in case.
    return text[:4000]


def dt_with_tz(t: _dt.time, base: _dt.date | None = None) -> _dt.datetime:
    """Combine a naive time with BD_TZ at today's date."""
    base = base or _dt.date.today()
    return BD_TZ.localize(_dt.datetime.combine(base, t))
