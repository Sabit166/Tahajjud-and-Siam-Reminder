# ============================================================
#  DHIKR & TAHAJJUD TELEGRAM BOT
#  Built for your Islamic accountability group
# ============================================================

from __future__ import annotations

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    PollAnswerHandler,
    filters,
    ChatMemberHandler,
)

import asyncio
import uvicorn

from config import (
    TOKEN,
    SUPABASE_URL,
    SUPABASE_API_KEY,
    WEB_HOST,
    WEB_PORT,
    log,
)
from db import init_db, cleanup_old_active_polls
from handlers import handle_poll_answer, handle_new_member, handle_admin_command, handle_my_chat_member
from scheduler import setup_scheduler
from web_server import app as web_app, set_bot_instance

# ============================================================
#  MAIN ENTRYPOINT
# ============================================================

def main():
    if not TOKEN:
        print("\nERROR: BOT_TOKEN is missing in your environment or .env file!\n")
        return

    if not SUPABASE_URL or not SUPABASE_API_KEY:
        print("\nERROR: SUPABASE_URL or SUPABASE_API_KEY is missing in your .env file!\n")
        return

    # Verify the schema and load poll configurations
    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler(["admin", "setup", "dashboard", "polls"], handle_admin_command))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_member))
    app.add_handler(ChatMemberHandler(handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    setup_scheduler(app)

    async def _post_init(ctx):
        set_bot_instance(app.bot, app.job_queue)
        cleanup_old_active_polls(24)

        # Start FastAPI Web Server concurrently in the same asyncio event loop
        server_config = uvicorn.Config(
            web_app,
            host=WEB_HOST,
            port=WEB_PORT,
            log_level="info",
            access_log=False,
        )
        server = uvicorn.Server(server_config)
        asyncio.create_task(server.serve())
        log.info(
            "📱 Mobile Poll Manager & API running on http://%s:%d",
            WEB_HOST,
            WEB_PORT,
        )

    app.job_queue.run_once(_post_init, when=1)

    log.info("Bot is running! Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()
