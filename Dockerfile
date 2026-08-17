FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY config.py practices.py db.py scheduling.py quran.py messaging.py jobs.py handlers.py scheduler.py main.py ./

CMD ["python", "main.py"]
