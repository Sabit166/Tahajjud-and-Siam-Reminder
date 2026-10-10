# Dhikr & Tahajjud Telegram Bot

A Telegram bot that helps groups keep up their daily and weekly amal together. For each group it:

- sends scheduled check-in polls ("Did you pray Tahajjud?"), at fixed times or relative to Dhaka prayer times,
- records each member's answer, keeps streaks, and replies with the marks earned,
- posts daily and weekly leaderboards,
- sends a hadith at each prayer time and a Jumu'ah sunnah reminder,
- gives each group's admins their own dashboard (a Telegram Mini App) to manage everything.

One bot serves many groups. Each group has its own polls, answers, streaks, and leaderboards, and one group never sees another group's data.

**Contents**

- [Admin guide: setting up and managing a group](#admin-guide)
- [Member guide](#member-guide)
- [Troubleshooting](#troubleshooting)
- [Operator guide: running and deploying the bot](#operator-guide)

---

<a id="admin-guide"></a>
## Admin guide: setting up and managing a group

### 1. Add the bot to your group

1. Open the group in Telegram, go to **Add members**, and add the bot.
2. Make sure members can see and vote on polls. The bot doesn't need admin rights.

The bot registers the group automatically when it's added.

### 2. Open your group's dashboard

Only **Telegram administrators of the group** can do this.

1. In the group, type `/` and choose **`/setup`**, or just send `/setup`. The command shows up in the `/` menu for admins only.
2. The bot replies with **💬 Open private chat with bot**. Tap it.
3. In the private chat, tap **Start**. The bot replies *"Your group setup link is verified."*
4. Tap **📱 Open Poll Dashboard**. The dashboard for **the group where you sent `/setup`** opens inside Telegram.

To manage a different group, send `/setup` in *that* group.

Good to know:

| Rule | What it means for you |
|---|---|
| Each dashboard button works **once**, within **10 minutes** | An older "Open Poll Dashboard" button shows *"Invalid, expired, or unauthorized setup token"*. Send `/setup` again. |
| A dashboard session lasts **60 minutes** | After that the setup screen appears. Send `/setup` again. |
| The link only works for the admin who sent `/setup` | Forwarding it to someone else won't give them access. |
| It must be opened inside Telegram | Pasting the dashboard URL into a browser won't log you in. |

`/admin`, `/dashboard` and `/polls` do the same thing as `/setup`.

### 3. A new group starts empty

A newly set-up group has **no polls**, and the bot sends it nothing until you add some. Use the dashboard to create the polls, reports, and reminders you want (see below).

### 4. Using the dashboard

The top of the dashboard shows the live Dhaka time and today's prayer times. Below that is the list of everything scheduled for your group. Each item shows its type, schedule, today's calculated time, and marks.

**Add an item.** Tap the add button and fill in:

| Field | Meaning |
|---|---|
| **Type** | **Amal poll**: a check-in poll members answer. **Report**: a leaderboard. **Reminder**: a text reminder. |
| **Title** | For an amal poll, this is the question members see. For reports and reminders, see the note below. |
| **Answer options** | Amal polls have exactly **two** options. The **first** means "done" and earns marks; the second means "missed". |
| **Time** | **Fixed**: a clock time in 24h format, e.g. `21:30`. **Prayer-relative**: Fajr, Sunrise, Dhuhr, Asr, Maghrib or Isha, plus an offset before or after, e.g. *30 min after Maghrib*. |
| **Days** | The weekdays it runs on, e.g. Mon & Thu for Sawm or Friday for Surah Kahf. |
| **Marks** | Points a member earns on the leaderboard for answering "done". Amal polls only. |
| **Active** | Turn this off to pause an item without deleting it. |

> **Reports and reminders must use these exact titles.** The bot recognises them by title. Any other title is sent as an ordinary poll instead.
>
> | Title | What it does |
> |---|---|
> | `Daily Report` | Daily leaderboard |
> | `Weekly Report` | Weekly leaderboard |
> | `Jumuah Reminder` | Yaum al-Jumu'ah sunnah reminder |

**Edit, pause or delete:** tap an item to edit it, use its switch to pause or resume it, or delete it. Changes are saved straight away and survive bot restarts. If a save fails, the dashboard shows an error. Your change has **not** been applied until it saves without one.

**⚡ Send now:** sends that poll, report, or reminder to your group immediately. Use it to test a new item.

### 5. How scoring works

- **Answering a poll:** the first option ("done") records the amal as completed and earns that poll's marks. The second option records it as missed. Members can change their vote, and the latest answer for the day counts.
- **Daily Report:** covers the 24 hours before the report is sent. Each member's score is the total marks of the polls they marked done, out of the maximum for that day's scheduled polls. Each line also shows their current streak.
- **Weekly Report:** covers the last 7 days. For every amal it shows how many times each member completed it out of the number of scheduled days, with marks, ranked by total marks.
- **Streaks:** count consecutive scheduled days a member completed an amal. Answering "missed" resets the streak to 0.

### 6. Automatic messages

| Message | When |
|---|---|
| Check-in polls | At each poll's configured time and days. Polls close automatically after 24 hours. |
| Thank-you reply ("MashaAllah …") | After each answer. Deleted automatically after 10 seconds. |
| Hadith reminder | At each of the five daily prayers |
| Welcome message | When a new member joins |

---

<a id="member-guide"></a>
## Member guide

- When a poll appears, tap your answer. The **first** option means you did it; the **second** means you missed it. Polls are not anonymous, because the bot needs to know who answered.
- You can change your answer while the poll is open. Your latest answer for the day counts.
- The bot confirms with a short message that disappears after 10 seconds.
- Check the daily and weekly reports to see your marks and streaks.

---

<a id="troubleshooting"></a>
## Troubleshooting

| Problem | What to do |
|---|---|
| `/setup` isn't in the `/` menu | Only group admins see it. Close and reopen the chat, since Telegram caches the menu. Typing `/setup` works either way. |
| *"Only Telegram group administrators can open the dashboard."* | Ask a group admin to run `/setup`, or ask them to make you an admin. |
| *"Please use the dashboard link generated by /setup inside your Telegram group."* | The private-chat link was old or for another account. Send `/setup` in the group again. |
| Dashboard shows *"Telegram Setup Required"* with *"Invalid, expired, or unauthorized setup token"* | The button was already used or is older than 10 minutes. Send `/setup` again. |
| Dashboard shows *"Open this setup link from Telegram."* | You opened the link outside Telegram. Use the button in the bot chat. |
| The group gets no polls | Check the dashboard: does the group have active polls scheduled for today's weekday? A new group starts empty. |
| A "report" arrived as a poll | Its title isn't exactly `Daily Report`, `Weekly Report` or `Jumuah Reminder`. Delete it and create it again with the exact title. |
| Answers aren't being counted | Make sure members answer polls the bot sent in the last 24 hours. Answers to closed or very old polls aren't recorded. If it continues, the operator should check the logs (see below). |

---

<a id="operator-guide"></a>
## Operator guide: running and deploying the bot

### Architecture

A single Python process runs three things in one event loop:

1. **Telegram bot** (python-telegram-bot, long polling): commands, poll answers, and group membership changes.
2. **Scheduler** (JobQueue): every minute it loops over all active groups and sends any due polls, reports, and reminders. Every 5 minutes it sends prayer-time hadiths.
3. **Web server** (FastAPI + Uvicorn): the dashboard API and the Mini App frontend in `static/`.

All data lives in **Supabase** (PostgreSQL). Every table is keyed by the group's Telegram chat ID (`group_chat_id`).

| File | Responsibility |
|---|---|
| `main.py` | Entry point. Wires the bot, scheduler, and web server, and registers the `/setup` command menu. |
| `config.py` | Environment variables, constants, logging |
| `db.py` | Supabase access: groups, poll configs, active polls, responses, streaks, setup tokens, sessions |
| `handlers.py` | Telegram handlers: `/setup`, private `/start`, poll answers, new members, errors |
| `messaging.py` | Sends polls, reports, reminders, and hadiths. Contains the per-group schedule tick. |
| `jobs.py` / `scheduler.py` | Recurring jobs that run the tick for each group |
| `scheduling.py` | Closes polls and deletes reply messages after a delay |
| `web_server.py` | Dashboard REST API and Mini App authentication |
| `static/` | Dashboard frontend (`index.html`, `app.js`, `styles.css`) |
| `prayer_times.py` / `hadith.py` | Aladhan prayer times (Dhaka) and HadithAPI client |
| `practices.py` | Static text (welcome list, Jumu'ah sunnahs) |
| `supabase_schema.sql` | Database schema, including the multi-group migration |
| `tests/` | Automated tests (no network needed) |

#### Data model

| Table | Holds | Key |
|---|---|---|
| `groups` | Every group the bot is in | `chat_id` |
| `poll_configs` | Each group's polls, reports, and reminders | `(group_chat_id, id)` |
| `active_polls` | Telegram poll ID → poll config, for matching answers to polls | `(group_chat_id, poll_id)` |
| `responses` | One answer per member, amal, and day | unique `(group_chat_id, user_id, practice, response_date)` |
| `streaks` | Current and longest streak per member and amal | `(group_chat_id, user_id, practice)` |
| `setup_tokens` | One-time `/setup` links (10 min) | `token` |
| `web_sessions` | Dashboard sessions (60 min) | `token` |

#### How dashboard access is secured

1. `/setup` checks that the sender is a group admin, then creates a one-time token tied to that group and that admin.
2. The private chat checks the token belongs to the user and opens the Mini App with it.
3. The backend verifies Telegram's signed `initData` using the bot token. It then uses the token only if it belongs to the same Telegram user, and issues a session for **the token's group**. The browser can never choose which group it manages.

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | yes | Telegram bot token from @BotFather |
| `SUPABASE_URL` | yes | Supabase project URL (with or without `/rest/v1/`) |
| `SUPABASE_API_KEY` | yes | Supabase **service-role** key (bypasses RLS) |
| `WEB_APP_URL` | yes, for the dashboard | Public **HTTPS** URL of the web server, e.g. `https://your-app.up.railway.app`. Without it, `/setup` can't offer the Mini App button. |
| `HADITH_API_KEY` | for hadith reminders | Key from hadithapi.com |
| `BD_TZ` | no | Time zone, default `Asia/Dhaka` |
| `PORT` / `WEB_PORT` | no | Web server port, default `8000` (Railway sets `PORT`) |
| `WEB_HOST` | no | Bind address, default `0.0.0.0` |

Fixed settings in `config.py`: polls close after 24 hours, and reply messages are deleted after 10 seconds.

### Supabase setup (one time)

1. Create a project at [supabase.com](https://supabase.com).
2. From **Project Settings → API**, copy the **Project URL** into `SUPABASE_URL` and the **service_role** key into `SUPABASE_API_KEY`.
3. Open the **SQL Editor**, paste the whole of `supabase_schema.sql`, and run it.

The bot does not migrate the schema itself. If you change the schema, add the statements to `supabase_schema.sql` and run them in the SQL editor **before** deploying code that depends on them. Running a migration while older code is still live causes failed writes.

### Run locally

```powershell
python -m venv env
env\Scripts\activate
pip install -r requirements.txt
# create .env with the variables above
python main.py
```

The dashboard is served on `http://localhost:8000`. The Mini App sign-in only works through Telegram over a public HTTPS `WEB_APP_URL`, so expect the dashboard to show the setup screen when you open it locally.

> Only one copy of the bot can poll Telegram at a time. Running it locally while production is live causes `Conflict: terminated by other getUpdates request`. Use a separate test bot token for local work.

### Run the tests

```powershell
pip install pytest
python -m pytest tests
```

The tests use in-memory fakes for Supabase and Telegram, so they need no network access or credentials. They cover:

- the poll send → answer → record flow,
- the scheduler,
- daily and weekly reports,
- dashboard persistence,
- Mini App authentication,
- isolation between groups.

### Deploy on Railway (current production)

The Railway service is connected to this GitHub repository and builds from the `Dockerfile`. **Every push to `main` deploys automatically.**

1. In the Railway service's **Variables** tab, set the [environment variables](#environment-variables).
2. Give the service a public domain (**Settings → Networking**) and set `WEB_APP_URL` to it.
3. Push to `main`. Then check:

```powershell
railway deployment list      # status of recent deployments
railway logs -d -n 200       # logs of the running deployment
```

After a deploy, a healthy startup log includes:

```
Database (Supabase) ready.
Supabase poll_configs table available.
Registered /setup in the command menu for group administrators.
Bot is running!
```

A single `Conflict: terminated by other getUpdates request` right after a deploy is normal: the old container is still shutting down. It should not repeat.

**Rollback:** in the Railway dashboard, open the previous successful deployment and choose **Redeploy**.

### Run with Docker on your own server

```bash
docker compose up -d --build
docker compose logs -f
```

`docker-compose.yml` reads `.env` and exposes port `8000`. For the dashboard to work, put the server behind HTTPS (e.g. a reverse proxy such as Caddy or Nginx with a certificate) and set `WEB_APP_URL` to that HTTPS address. Telegram only opens Mini Apps over HTTPS. All data is in Supabase, so containers can be rebuilt freely.

### Logs and diagnostics

- Every unhandled error is logged as `Unhandled error in update|job …` with the poll ID, chat ID, or job name involved.
- If one group's scheduled run fails, it's logged as `Schedule tick failed for group <id>`, and the other groups carry on.
- `Received poll answer … for untracked poll ID …` means the answer was for a poll the bot has no record of (e.g. older than 24 hours). Nothing is recorded.

### Security

- `SUPABASE_API_KEY` is a service-role key with full database access, and `BOT_TOKEN` controls the bot. Keep both in Railway variables or an untracked `.env`. Never commit, paste, or screenshot them.
- The `httpx` request logger is set to WARNING because Telegram and HadithAPI URLs contain secrets. Don't lower it.
- If a secret leaks, rotate it: `BOT_TOKEN` via @BotFather (`/revoke`), the Supabase key in the Supabase dashboard, and the HadithAPI key at hadithapi.com. Then update the Railway variables.
