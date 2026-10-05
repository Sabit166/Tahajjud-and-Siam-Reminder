"""Aladhan prayer-time fetching and timezone helpers."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

import httpx

from config import BD_TZ, log

ALADHAN_BASE = "https://api.aladhan.com/v1"
DEFAULT_CITY = "Dhaka"
DEFAULT_COUNTRY = "Bangladesh"
DEFAULT_METHOD = 1  # University of Islamic Sciences, Karachi


@dataclass
class PrayerTimes:
    sunrise: _dt.time
    fajr: _dt.time
    dhuhr: _dt.time
    asr: _dt.time
    maghrib: _dt.time
    isha: _dt.time

    def as_dict(self) -> dict[str, _dt.time]:
        return {
            "sunrise": self.sunrise,
            "fajr": self.fajr,
            "dhuhr": self.dhuhr,
            "asr": self.asr,
            "maghrib": self.maghrib,
            "isha": self.isha,
        }


async def fetch_prayer_times(
    city: str = DEFAULT_CITY,
    country: str = DEFAULT_COUNTRY,
    method: int = DEFAULT_METHOD,
    date: _dt.date | None = None,
    client: httpx.AsyncClient | None = None,
) -> PrayerTimes:
    """Fetch today's prayer times for the configured city from Aladhan."""
    when = date or _dt.date.today()
    url = f"{ALADHAN_BASE}/timingsByCity/{when.isoformat()}"
    params = {"city": city, "country": country, "method": method}
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    try:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    finally:
        if own_client:
            await client.aclose()

    timings = data.get("data", {}).get("timings", {})

    def _time(key: str) -> _dt.time:
        raw = timings.get(key, "00:00").split(" ")[0]
        hour, minute = raw.split(":")[:2]
        return _dt.time(int(hour), int(minute))

    log.info(
        "Aladhan prayer times for %s on %s: %s",
        city,
        when,
        {key: timings.get(key) for key in ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")},
    )
    return PrayerTimes(
        sunrise=_time("Sunrise"),
        fajr=_time("Fajr"),
        dhuhr=_time("Dhuhr"),
        asr=_time("Asr"),
        maghrib=_time("Maghrib"),
        isha=_time("Isha"),
    )


def dt_with_tz(t: _dt.time, base: _dt.date | None = None) -> _dt.datetime:
    """Combine a naive time with BD_TZ at today's date."""
    base = base or _dt.date.today()
    return BD_TZ.localize(_dt.datetime.combine(base, t))
