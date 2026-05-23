# Dhikr & Tahajjud Telegram Bot

## Run locally

1. Fill in `.env` with your Telegram bot token and group chat ID.
2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Start the bot:

```powershell
python script.py
```

## Run with Docker

1. Make sure `.env` contains valid values for `BOT_TOKEN` and `GROUP_CHAT_ID`.
2. Build and start the container:

```powershell
docker compose up -d --build
```

3. Follow logs:

```powershell
docker compose logs -f
```

The SQLite database is stored in `./data/dhikr_records.db` on the host.

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
DB_PATH=/data/dhikr_records.db
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
- The SQLite database is persisted in the `data` folder mounted into the container.

## Deploy on Railway

Railway can run this bot from the included `Dockerfile`, but there is one important detail: the bot uses SQLite, so you need persistent storage for `DB_PATH` or your database can be lost on redeploys.

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
DB_PATH=/data/dhikr_records.db
```

6. If Railway offers a persistent volume for your plan, mount it at `/data` so the SQLite file survives restarts.
7. Deploy the service.

### If Railway volume support is not available

Use Railway PostgreSQL instead of SQLite, or move to a host that supports persistent disk. Without persistent storage, the bot will still run, but your check-in history can disappear when Railway recreates the container.

### What to expect

- The bot does not need an exposed web port because it uses Telegram polling.
- Railway only needs to run the container continuously.
- `restart: unless-stopped` is useful on Docker hosts, but Railway manages restarts for you.
