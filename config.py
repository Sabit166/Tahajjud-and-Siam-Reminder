"""
Environment loading, settings/constants, and logging setup for the
Dhikr & Tahajjud Telegram Bot.
"""

import os
import logging
from pathlib import Path

import pytz

# ============================================================
#  LOAD ENVIRONMENT VARIABLES
# ============================================================

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    env_path = Path(".env")
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

# ============================================================
#  SETTINGS & CONFIGURATION
# ============================================================

TOKEN = os.getenv("BOT_TOKEN", "")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "0"))
BD_TZ = pytz.timezone(os.getenv("BD_TZ", "Asia/Dhaka"))

# Supabase REST endpoint and the service-role / secret key.
# The service-role key bypasses RLS so we don't need any policy setup.
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_API_KEY = os.getenv("SUPABASE_API_KEY", "")

RESPONSE_WINDOW_HOURS = 24
RESPONSE_DELETE_AFTER_SECONDS = 10
DAILY_REPORT_HOUR = 18
DAILY_REPORT_MINUTE = 30

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger(__name__)
