-- ============================================================
--  Dhikr & Tahajjud Bot — Supabase Schema
--  Run this once in the Supabase SQL editor.
-- ============================================================

-- 1. RESPONSES (one row per user/practice/day; upsert on conflict)
create table if not exists public.responses (
    id              bigserial primary key,
    user_id         bigint      not null,
    username        text,
    full_name       text        not null,
    practice        text        not null,
    did_it          boolean     not null,
    response_date   date        not null,
    recorded_at     timestamptz not null default now(),
    constraint responses_user_practice_date_unique
        unique (user_id, practice, response_date)
);

create index if not exists responses_recorded_at_idx
    on public.responses (recorded_at desc);

create index if not exists responses_practice_date_idx
    on public.responses (practice, response_date);

-- 2. ACTIVE POLLS (Telegram poll_id -> practice_key)
create table if not exists public.active_polls (
    poll_id         text primary key,
    practice_key    text        not null,
    created_at      timestamptz not null default now()
);

create index if not exists active_polls_created_at_idx
    on public.active_polls (created_at);

-- 3. STREAKS (one row per user/practice)
create table if not exists public.streaks (
    user_id              bigint  not null,
    practice             text    not null,
    current_streak       integer not null default 0,
    longest_streak       integer not null default 0,
    last_done_date       date,
    last_scheduled_date  date,
    updated_at           timestamptz not null default now(),
    primary key (user_id, practice)
);

-- 4. Auto-touch updated_at on streaks
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

drop trigger if exists streaks_touch_updated_at on public.streaks;
create trigger streaks_touch_updated_at
    before update on public.streaks
    for each row execute function public.touch_updated_at();

-- 5. Convenience view: latest response per user/practice
create or replace view public.latest_responses as
select distinct on (user_id, practice)
    user_id, practice, did_it, response_date, recorded_at, full_name
from public.responses
order by user_id, practice, recorded_at desc;

-- Done. Tables are public.* and the service-role key bypasses RLS,
-- so the bot can read/write without any extra policy work.