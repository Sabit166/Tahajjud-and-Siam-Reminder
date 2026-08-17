# Dhikr & Tahajjud Telegram Bot

## Project structure

The bot is split into focused modules instead of one large script:

| File | Responsibility |
|---|---|
| `main.py` | Entrypoint — wires everything together and starts polling |
| `config.py` | Env loading, settings/constants, logging setup |
| `practices.py` | Static data: practice definitions, labels, poll options |
| `db.py` | Supabase (PostgREST) client: schema, active-poll tracking, response/report queries |
| `scheduling.py` | Low-level JobQueue helpers for closing polls / deleting messages after a delay |
| `messaging.py` | Builds and sends check-in polls and daily/weekly reports |
| `jobs.py` | JobQueue callback wrappers that trigger check-ins and reports on schedule |
| `handlers.py` | Telegram update handlers (poll answers, new member welcome) |
| `scheduler.py` | Registers all recurring jobs (`setup_scheduler`) |

## Run locally

1. Fill in `.env` with your Telegram bot token, group chat ID, and your Supabase URL/key (see the next section).
2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Start the bot:

```powershell
python main.py
```

## Database: Supabase (PostgreSQL)

The bot stores all check-in history, active polls, and streaks in a **Supabase** project. This replaces the old SQLite file so data survives across redeploys. There is no local DB file anywhere in the project.

### One-time setup

1. Create a free project at [supabase.com](https://supabase.com).
2. In the Supabase dashboard, open **Project Settings -> API** and copy:
   - **Project URL** — set as `SUPABASE_URL` in `.env`
   - **service_role / secret key** — set as `SUPABASE_API_KEY` in `.env`
   - (The service-role key bypasses Row Level Security and is required for the bot to read/write all tables.)
3. Open the **SQL Editor** in the Supabase dashboard and paste the contents of `supabase_schema.sql`, then run it. This creates the three tables, indexes, and a trigger.

### Required `.env` keys

```env
BOT_TOKEN=your_telegram_bot_token
GROUP_CHAT_ID=-1001234567890
BD_TZ=Asia/Dhaka
SUPABASE_URL=https://YOURPROJECT.supabase.co/rest/v1/
SUPABASE_API_KEY=sb_secret_...your_service_role_key...
```

### Security note

`SUPABASE_API_KEY` is a **service-role** key — anyone who has it can read, modify, or delete every row in your database. Treat it like a database password:

- Never commit `.env` to git (already in `.gitignore`).
- Never paste it into chats, tickets, or screenshots.
- For production, prefer storing it in a secret manager (Railway/Render/Oracle env vars) rather than a file on disk.
- If you ever leak it, rotate it immediately from the Supabase dashboard.

### Schema migrations

If you change `db.py` in a way that needs new tables or columns, edit `supabase_schema.sql` and run the new statements in the SQL editor. The existing bot does not auto-migrate.

## Run with Docker

1. Make sure `.env` contains valid values for `BOT_TOKEN`, `GROUP_CHAT_ID`, `SUPABASE_URL`, and `SUPABASE_API_KEY`.
2. Build and start the container:

```powershell
docker compose up -d --build
```

3. Follow logs:

```powershell
docker compose logs -f
```

There is no local database file or volume — all data lives in Supabase and survives container rebuilds.

## Deploy on Oracle Cloud Free Tier

This bot is a good fit for an Oracle Cloud Always Free Ubuntu instance because it uses Docker Compose, runs in the background, and only needs outbound internet access to Telegram.

### 1. Create the instance

1. Create an Oracle Cloud account and open the Compute section.
2. Launch an Always Free Ubuntu VM.
3. Keep the default SSH key download and save it safely.

### 2. Connect to the VM

Use SSH from your local machine:

```bash
ssh -i path/to/your/private_key ubuntu@YOUR_PUBLIC_IP
```

### 3. Install Docker and Git

On the VM, run:

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin git
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
```

Then log out and SSH back in so the docker group change applies.

### 4. Get the project onto the server

Either clone the repository or copy the files to the VM. For a Git repo:

```bash
git clone YOUR_REPO_URL dhikr-bot
cd dhikr-bot
```

### 5. Set environment values

Create or edit `.env` on the VM so it contains your real values:

```env
BOT_TOKEN=your_telegram_bot_token
GROUP_CHAT_ID=-1001234567890
BD_TZ=Asia/Dhaka
SUPABASE_URL=https://YOURPROJECT.supabase.co/rest/v1/
SUPABASE_API_KEY=sb_secret_...your_service_role_key...
```

### 6. Start the bot

Run Docker Compose:

```bash
docker compose up -d --build
docker compose logs -f
```

### 7. Confirm it survives reboots

The `restart: unless-stopped` setting in `docker-compose.yml` makes Docker bring the bot back after a reboot or crash.

### Notes for Oracle Free Tier

- You do not need to open any public application port for this bot because it uses Telegram polling.
- Only SSH access is required for administration.
- Database storage is handled by Supabase, so there is nothing to back up locally.

## Deploy on Railway

Railway can run this bot from the included `Dockerfile`. Because the database is now Supabase (managed, external), there is no persistent-volume concern — every redeploy starts with the same data.

### Recommended setup

1. Push this project to GitHub if it is not already there.
2. In Railway, create a new project and choose **Deploy from GitHub repo**.
3. Select this repository.
4. Railway should detect the `Dockerfile` automatically.
5. Add the following environment variables in the Railway service settings:

```env
BOT_TOKEN=your_telegram_bot_token
GROUP_CHAT_ID=-1001234567890
BD_TZ=Asia/Dhaka
SUPABASE_URL=https://YOURPROJECT.supabase.co/rest/v1/
SUPABASE_API_KEY=sb_secret_...your_service_role_key...
```

6. Deploy the service. Railway will rebuild on every push.

### What to expect

- The bot does not need an exposed web port because it uses Telegram polling.
- Railway only needs to run the container continuously.
- `restart: unless-stopped` is useful on Docker hosts, but Railway manages restarts for you.

## Auto-deploy with a cron job (pull every hour)

On a server you manage yourself (e.g. the Oracle Cloud VM above), you can automatically pull the latest commit and redeploy every hour using cron. This uses the included `deploy.sh` script, which:

- Fetches and resets to the latest commit on `origin/main`
- Only rebuilds the Docker image if a new commit was actually pulled
- Logs its output to `deploy.log` in the project directory
- The database is in Supabase, so the deploy script never touches local storage

### 1. Make the script executable

On the server, inside the project directory:

```bash
chmod +x deploy.sh
```

### 2. Test it manually first

```bash
./deploy.sh
cat deploy.log
```

Confirm it pulls correctly and the bot container restarts (`docker compose ps`).

### 3. Add the cron job

Open your crontab:

```bash
crontab -e
```

Add this line, replacing `/home/ubuntu/dhikr-bot` with the actual full path to your project directory on the server:

```cron
0 0 * * * cd /home/ubuntu/dhikr-bot && ./deploy.sh
```

This runs `deploy.sh` at the top of every hour. Save and exit — cron picks it up automatically.

### 4. Verify it's scheduled

```bash
crontab -l
```

### Notes

- If your default branch isn't `main`, edit the `git reset --hard origin/main` line in `deploy.sh` to match (e.g. `origin/master`).
- `deploy.log` will grow over time; rotate or truncate it periodically if you want (e.g. `> deploy.log` monthly, or point it through `logrotate`).
- Since `deploy.sh` uses `git reset --hard`, any *uncommitted* local changes in the project directory on the server will be discarded on each run — keep the server checkout clean and make all changes through your repo.
