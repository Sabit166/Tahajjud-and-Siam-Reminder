# ============================================================
#  DHIKR & TAHAJJUD TELEGRAM BOT
#  Built for your Islamic accountability group
# ============================================================

from telegram.ext import Application, MessageHandler, PollAnswerHandler, filters

from config import TOKEN, GROUP_CHAT_ID, SUPABASE_URL, SUPABASE_API_KEY, log
from db import init_db, cleanup_old_active_polls
from handlers import handle_poll_answer, handle_new_member
from scheduler import setup_scheduler

# ============================================================
#  MAIN ENTRYPOINT
# ============================================================

def main():
    if not TOKEN:
        print("\nERROR: BOT_TOKEN is missing in your environment or .env file!\n")
        return

    if GROUP_CHAT_ID == 0:
        print("\nERROR: GROUP_CHAT_ID is missing in your environment or .env file!\n")
        return

    if not SUPABASE_URL or not SUPABASE_API_KEY:
        print("\nERROR: SUPABASE_URL or SUPABASE_API_KEY is missing in your .env file!\n")
        return

    # Verify the schema is in place before we start polling. This will
    # fail loudly if the DDL hasn't been run on Supabase yet.
    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_member))

    setup_scheduler(app)

    # Stale-poll cleanup runs once after the scheduler is up so the
    # first poll dispatch doesn't race it.
    async def _post_init(ctx):
        cleanup_old_active_polls(24)

    app.job_queue.run_once(_post_init, when=5)

    log.info("Bot is running! Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()
