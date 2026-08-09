# ============================================================
#  DHIKR & TAHAJJUD TELEGRAM BOT
#  Built for your Islamic accountability group
# ============================================================

from telegram.ext import Application, MessageHandler, PollAnswerHandler, filters

from config import TOKEN, GROUP_CHAT_ID, log
from db import init_db, cleanup_old_active_polls
from handlers import handle_poll_answer, handle_new_member
from scheduler import setup_scheduler

init_db()
cleanup_old_active_polls(24)

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

    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(PollAnswerHandler(handle_poll_answer))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_member))

    setup_scheduler(app)

    log.info("Bot is running! Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()
