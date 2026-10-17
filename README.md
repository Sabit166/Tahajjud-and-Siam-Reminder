# Dhikr & Tahajjud Telegram Bot

A Telegram bot that helps groups keep up their daily and weekly amal together. For each group it:

- sends scheduled check-in polls ("Did you pray Tahajjud?"), at fixed times or relative to Dhaka prayer times,
- records each member's answer, keeps streaks, and replies with the marks earned,
- posts a daily leaderboard (2 minutes after Maghrib), an inactivity callout of members who answered none of the day's polls (1 minute later), and a weekly leaderboard (Friday, 5 minutes after Maghrib),
- sends a hadith at each prayer time,
- gives each group's admins their own dashboard (a Telegram Mini App) to manage everything.

One bot serves many groups. Each group has its own polls, answers, streaks, and leaderboards, and one group never sees another group's data.

**Add it to your group:** the bot is **Muezzin**, username [**@Muaddhin_Ilal_Furqan_Bot**](https://t.me/Muaddhin_Ilal_Furqan_Bot). See [step 1 of the admin guide](#admin-guide) below.

**Contents**

- [Admin guide: setting up and managing a group](#admin-guide)
- [Member guide](#member-guide)
- [Troubleshooting](#troubleshooting)
- [Operator guide: running and deploying the bot](#operator-guide)

---

<a id="admin-guide"></a>
## Admin guide: setting up and managing a group

### 1. Add the bot to your group

1. Open the group in Telegram, go to **Add members**, search for **@Muaddhin_Ilal_Furqan_Bot** (shown as *Muezzin*), and add it. Or open [t.me/Muaddhin_Ilal_Furqan_Bot](https://t.me/Muaddhin_Ilal_Furqan_Bot), tap the bot's name, and choose **Add to Group**.
2. Make sure members can see and vote on polls. To let the bot remove members after 30 days of inactivity (see below), make it an **admin with the "Ban users" permission**. Everything else works without admin rights.

The bot registers the group automatically when it's added, and **immediately posts the setup message** with the **Open private chat with bot** button. Any admin can continue from step 2 below by tapping it. When someone taps it and presses Start, the bot checks that they are an administrator of the group; anyone else is refused.

### 2. Open your group's dashboard

Only **Telegram administrators of the group** can do this.

1. In the group, type `/` and choose **`/setup`**, or just send `/setup`. The command shows up in the `/` menu for admins only. (Right after adding the bot you can skip this and use the button from its welcome message instead.)
2. The bot replies with **💬 Open private chat with bot**. Tap it.
3. In the private chat, tap **Start**. The bot replies *"Your group setup link is verified."*
4. Tap **📱 Open Poll Dashboard**. The dashboard for **the group where you sent `/setup`** opens inside Telegram.

To manage a different group, send `/setup` in *that* group.

Good to know:

| Rule | What it means for you |
|---|---|
| Each dashboard button works **once**, within **10 minutes** | An older "Open Poll Dashboard" button shows *"Invalid, expired, or unauthorized setup token"*. Send `/setup` again. |
| A dashboard session lasts **60 minutes** | After that the setup screen appears. Send `/setup` again. |
| The `/setup` link only works for the admin who sent it; the welcome-message link works for any admin of the group | Non-admins are refused, so forwarding a link doesn't give anyone access. |
| It must be opened inside Telegram | Pasting the dashboard URL into a browser won't log you in. |

`/admin`, `/dashboard` and `/polls` do the same thing as `/setup`.

### 3. A new group starts with no polls

A newly set-up group has **no amal polls**. Use the dashboard to add the ones your group wants (see below).

The **daily and weekly reports are automatic** for every group, so you don't set them up:

| Report | When | Covers |
|---|---|---|
| Daily Report | Every day, 2 minutes after Maghrib | That amal day: from the previous Maghrib to this Maghrib |
| Inactivity Callout | Every day, 3 minutes after Maghrib (right after the daily report) | Members who answered **none** of that amal day's polls |
| Weekly Report | Friday, 5 minutes after Maghrib | The last 7 days |

Reports only include **your group's members** and **your group's amal polls**. Until your group has polls and answers, the reports say that no responses were recorded.

About the **inactivity callout**:
- It mentions each inactive member by name, so they get a notification, and shows their **inactive streak**: how many poll days in a row they haven't answered anything, e.g. `Rafin (3 days)`. The longest streaks are listed first. Days without polls are skipped. The count stops at the member's last answer or the day they joined, and anything over 60 days shows as `60+ days`.
- Answering "missed" counts as taking part. Only members who answered nothing are listed.
- Telegram doesn't let bots see a group's full member list. "Members" means everyone the bot knows in the group: people recorded when they joined (from when this feature was added; removed again when they leave, while their past answers are kept for the reports), everyone who has ever answered a poll, and the group's admins. Only people still in the group are listed. Someone who joined before the feature and has never answered a poll isn't known to the bot yet.
- It's skipped on days when the group had no polls, or if that day's daily report wasn't sent (e.g. the bot was restarting at the time).

**Removal after 30 days of inactivity** (every group):
- From **27** inactive days, the callout warns: `(28 days, will be removed at 30)`.
- At **30** days, the member is listed as `(30 days, removed)` and **kicked**: removed from the group but not banned, so they can rejoin with an invite link. The bot posts who was removed. Their past answers are kept.
- **Admins and the group owner are never removed or warned.**
- Days when nobody in the group answered anything (e.g. the bot was down) don't count towards the 30.
- **The bot must be an admin with the "Ban users" permission** for this. Without it, nobody is removed, and the bot posts a message asking the admins to grant it.

Around Maghrib the messages always go out in this order, one at a time: Maghrib hadith (+0), Daily Report (+2), Inactivity Callout (+3), and on Fridays the Weekly Report (+5).

### 4. Using the dashboard

The top of the dashboard shows the live Dhaka time and today's prayer times. Below that is the list of your group's **amal polls**, each with its schedule, today's calculated time, and marks. The dashboard manages amal polls only; reports can't be edited.

**Add a poll.** Tap the add button and fill in:

| Field | Meaning |
|---|---|
| **Title** | The question members see, e.g. *Tahajjud Salat*. |
| **Answer options** | Exactly **two** options. The **first** means "done" and earns marks; the second means "missed". |
| **Time** | **Fixed**: a clock time in 24h format, e.g. `21:30`. **Prayer-relative**: Fajr, Sunrise, Dhuhr, Asr, Maghrib or Isha, plus an offset before or after, e.g. *30 min after Maghrib*. |
| **Days** | The weekdays it runs on, e.g. Mon & Thu for Sawm or Friday for Surah Kahf. |
| **Marks** | Points a member earns on the leaderboard for answering "done". |
| **Active** | Turn this off to pause a poll without deleting it. |

**Edit, pause or delete:** tap a poll to edit it, use its switch to pause or resume it, or delete it. Changes are saved straight away and survive bot restarts. If a save fails, the dashboard shows an error. Your change has **not** been applied until it saves without one.

**⚡ Send now:** sends that poll to your group immediately. Use it to test a new poll.

### 5. How scoring works

- **Answering a poll:** the first option ("done") records the amal as completed and earns that poll's marks. The second option records it as missed. Members can change their vote while the poll is open; the latest answer counts. **Retracting a vote removes it**, as if the member never answered, and their streak goes back to what it was.
- **Daily Report:** covers one **amal day**, from the previous Maghrib to this Maghrib. It includes the polls sent during that day and the answers given to them. A poll sent after Maghrib belongs to the next day; a poll that runs only on certain weekdays (e.g. Sawm on Mon & Thu) appears only in the report of the day it was sent. Each member's score is the total marks of the polls they marked done, out of the day's maximum. Each line also shows their current streak.
- **Weekly Report:** covers the last 7 amal days. For every amal it shows days performed out of scheduled days, e.g. `Morning Adhkar: (4/7)`, and members are ranked by total marks. Answers to polls the group has since deleted aren't counted.
- **Streaks:** count consecutive scheduled days a member completed an amal. Answering "missed" resets the streak to 0.

### 6. Automatic messages

| Message | When |
|---|---|
| Check-in polls | At each poll's configured time and days. Each poll closes when its amal day ends: at the next Maghrib + 2 minutes, when the daily report is sent. |
| Daily Report | Every day, 2 minutes after Maghrib |
| Inactivity Callout | Every day, 3 minutes after Maghrib |
| Weekly Report | Friday, 5 minutes after Maghrib |
| Thank-you reply ("MashaAllah …") | After each answer. Deleted automatically after 10 seconds. |
| Hadith reminder | At the start of each of the five daily prayers (within a minute) |
| Welcome message | When a new member joins. It lists your group's active amal polls (with their days, if not daily). |

---

<a id="member-guide"></a>
## Member guide

- When a poll appears, tap your answer. The **first** option means you did it; the **second** means you missed it. Polls are not anonymous, because the bot needs to know who answered.
- You can change your answer while the poll is open; if you retract your vote, it no longer counts. Each poll closes at Maghrib + 2 minutes, when the daily report is posted, so answer before then.
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
| The group gets no polls | Check the dashboard: does the group have active polls scheduled for today's weekday? A new group starts with none. |
| A report says no responses were recorded | The group has no amal polls yet, or nobody answered in that period. |
| Answers aren't being counted | Polls close at Maghrib + 2 minutes, when the daily report is sent, and later answers aren't recorded. If it continues, the operator should check the logs (see below). |

---

<a id="operator-guide"></a>
## Operator guide: running and deploying the bot

### Architecture

A single Python process runs three things in one event loop:

1. **Telegram bot** (python-telegram-bot, long polling): commands, poll answers, and group membership changes.
2. **Scheduler** (JobQueue): every minute it loops over all active groups and sends whatever became due, in time order: amal polls, a hadith at each prayer, the daily report, the inactivity callout, and the weekly report.
3. **Web server** (FastAPI + Uvicorn): the dashboard API and the Mini App frontend in `static/`.

All data lives in **Supabase** (PostgreSQL). Every table is keyed by the group's Telegram chat ID (`group_chat_id`).

| File | Responsibility |
|---|---|
| `main.py` | Entry point. Wires the bot, scheduler, and web server, and registers the `/setup` command menu. |
| `config.py` | Environment variables, constants, logging |
| `db.py` | Supabase access: groups, poll configs, active polls, responses, streaks, setup tokens, sessions |
| `handlers.py` | Telegram handlers: `/setup`, private `/start`, poll answers, new members, errors |
| `messaging.py` | Sends polls, reports, and hadiths. Contains the per-group schedule tick and the built-in report schedule (`BUILTIN_REPORTS`). |
| `jobs.py` / `scheduler.py` | Recurring jobs that run the tick for each group |
| `scheduling.py` | Closes polls and deletes reply messages after a delay |
| `web_server.py` | Dashboard REST API and Mini App authentication |
| `static/` | Dashboard frontend (`index.html`, `app.js`, `styles.css`) |
| `prayer_times.py` / `hadith.py` | Aladhan prayer times (Dhaka) and HadithAPI client |
| `supabase_schema.sql` | Database schema, including the multi-group migration |
| `tests/` | Automated tests (no network needed) |

#### Data model

| Table | Holds | Key |
|---|---|---|
| `groups` | Every group the bot is in | `chat_id` |
| `poll_configs` | Each group's amal polls. Older report/reminder rows may remain; the bot ignores them. | `(group_chat_id, id)` |
| `active_polls` | Telegram poll ID → poll config, send time, and message ID, for matching answers and closing polls | `(group_chat_id, poll_id)` |
| `responses` | One answer per member, amal, and day | unique `(group_chat_id, user_id, practice, response_date)` |
| `streaks` | Current and longest streak per member and amal | `(group_chat_id, user_id, practice)` |
| `group_members` | Members recorded when they join (removed when they leave), for the inactivity callout | `(group_chat_id, user_id)` |
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

Fixed settings: polls close at the end of their amal day (Maghrib + 2 minutes, `messaging.DAY_END_GRACE`), the reports and the callout run at the times in `messaging.BUILTIN_REPORTS`, and reply messages are deleted after 10 seconds (`config.py`).

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
- daily and weekly reports (timing, every group, per-group content),
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

`Poll closing restored: N closed (overdue), N scheduled, ...` shows the bot re-arming poll closing after the restart.

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
- `Received poll answer ... for untracked poll ID ...` means the answer was for a poll the bot has no record of. `Ignored late answer ...` means the poll's amal day had already ended. Nothing is recorded in either case.

### Security

- `SUPABASE_API_KEY` is a service-role key with full database access, and `BOT_TOKEN` controls the bot. Keep both in Railway variables or an untracked `.env`. Never commit, paste, or screenshot them.
- The `httpx` request logger is set to WARNING because Telegram and HadithAPI URLs contain secrets. Don't lower it.
- If a secret leaks, rotate it: `BOT_TOKEN` via @BotFather (`/revoke`), the Supabase key in the Supabase dashboard, and the HadithAPI key at hadithapi.com. Then update the Railway variables.
