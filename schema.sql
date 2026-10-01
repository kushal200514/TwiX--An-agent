create extension if not exists pgcrypto;

create table if not exists public.daily_updates (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  work_date date not null,
  raw_update text not null,
  structured jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists daily_updates_user_date_idx
  on public.daily_updates(user_id, work_date desc);

create table if not exists public.generated_posts (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  daily_update_id uuid not null references public.daily_updates(id) on delete cascade,
  post_text text not null,
  status text not null default 'draft' check (status in ('draft','published','failed')),
  x_post_id text,
  created_at timestamptz not null default now()
);

create index if not exists generated_posts_user_created_idx
  on public.generated_posts(user_id, created_at desc);
