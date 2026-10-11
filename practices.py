"""
Static definitions of the practices (amal) tracked by the bot.
"""

from __future__ import annotations

PRACTICES = {
    "morning_dhikr":  {"label": "Morning Adhkar",  "poll_options": ["Alhamdulillah, done", "Incomplete/Missed"]},
    "fazr_jamaat":   {"label": "Fazr Jamaat",      "poll_options": ["Alhamdulillah, done", "Missed Jamaat"]},
    "ishraq_salat":   {"label": "Ishraq Salat",      "poll_options": ["Alhamdulillah, done", "Missed"]},
    "quran":     {"label": "Read 2 ayah of the Quran", "poll_options": ["Alhamdulillah, done", "Missed"]},
    "evening_dhikr":  {"label": "Evening Adhkar",  "poll_options": ["Alhamdulillah, done", "Incomplete/Missed"]},
    "salawat_on_rasulullah": {"label": "Salawat on Rasulullah", "poll_options": ["Alhamdulillah, done", "Incomplete/Missed"]},
    "sawm":           {"label": "Sawm",           "poll_options": ["Alhamdulillah, fasting", "InshaAllah, next time"]},
    "surah_kahf":     {"label": "Surah Kahf",      "poll_options": ["Alhamdulillah, done", "Incomplete/Missed"]},
    "tahajjud":       {"label": "Tahajjud Salat",  "poll_options": ["Alhamdulillah, done", "InshaAllah, next time"]},
    "nightly_al_mulk": {"label": "Surat Al-Mulk", "poll_options": ["Alhamdulillah, done", "Missed"]},
    "nightly_as_sajdah": {"label": "Surat As-Sajdah", "poll_options": ["Alhamdulillah, done", "Missed"]},
    "nightly_al_baqarah_last_2": {"label": "Surat Al-Baqarah (Last 2 ayats)", "poll_options": ["Alhamdulillah, done", "Missed"]},
    "nightly_33_tasbeeh": {"label": "33x SubhanAllah, 33x Alhamdulillah, 34x AllahuAkbar", "poll_options": ["Alhamdulillah, done", "Missed"]},
    "istighfar_100x": {"label": "Istighfar 100x", "poll_options": ["Alhamdulillah, done", "Missed"]},
}

# Leaderboard weight for each amal. Practices not listed here retain the
# previous one-mark behavior.
AMAL_WEIGHTS: dict[str, int] = {
    "fazr_jamaat": 20,
    "tahajjud": 15,
    "sawm": 8,
    "istighfar_100x": 12,
    "salawat_on_rasulullah": 9,
    "morning_dhikr": 8,
    "evening_dhikr": 8,
    "ishraq_salat": 7,
    "quran": 7,
    "surah_kahf": 12,
    "nightly_al_mulk": 5,
    "nightly_as_sajdah": 5,
    "nightly_al_baqarah_last_2": 10,
    "nightly_33_tasbeeh": 3,
}

NIGHTLY_AMAL_OPTIONS = [
    "nightly_al_mulk",
    "nightly_as_sajdah",
    "nightly_al_baqarah_last_2",
    "nightly_33_tasbeeh",
]

# Set of nightly sub-practices. Each one counts as its OWN mark
# (1 mark per sub-practice, 4 marks total per night).
NIGHTLY_GROUP_PRACTICES = set(NIGHTLY_AMAL_OPTIONS)

# Practices that are scheduled only on certain weekdays. Streaks skip
# non-scheduled days instead of resetting.
SCHEDULED_WEEKDAYS: dict[str, set[int]] = {
    "sawm":       {0, 3},   # Monday (0) and Thursday (3)
    "surah_kahf": {4},      # Friday
}

