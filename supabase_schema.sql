-- Litreel core persistence schema for Supabase (PostgreSQL)
-- Run this in the Supabase SQL editor to provision the app tables.

create table if not exists public.users (
    id bigserial primary key,
    email varchar(255) not null unique,
    password_hash varchar(255) not null,
    created_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.projects (
    id bigserial primary key,
    user_id bigint references public.users(id) on delete set null,
    title varchar(255) not null,
    status varchar(50) not null default 'draft',
    active_concept_id bigint,
    voice varchar(50) not null default 'sarah',
    created_at timestamptz not null default timezone('utc', now()),
    supabase_book_id varchar(64)
);

create index if not exists idx_projects_user_id on public.projects(user_id);
create index if not exists idx_projects_supabase_book on public.projects(supabase_book_id);

create table if not exists public.concepts (
    id bigserial primary key,
    project_id bigint not null references public.projects(id) on delete cascade,
    name varchar(255) not null,
    description text not null,
    order_index integer not null default 0
);

create index if not exists idx_concepts_project_id on public.concepts(project_id);

create table if not exists public.slides (
    id bigserial primary key,
    concept_id bigint not null references public.concepts(id) on delete cascade,
    order_index integer not null default 0,
    text text not null,
    image_url text,
    effect varchar(50) not null default 'none',
    transition varchar(50) not null default 'fade'
);

create index if not exists idx_slides_concept_id on public.slides(concept_id);

create table if not exists public.slide_styles (
    id bigserial primary key,
    slide_id bigint not null references public.slides(id) on delete cascade,
    text_color varchar(32) not null default '#FFFFFF',
    outline_color varchar(32) not null default '#000000',
    font_weight varchar(8) not null default '700',
    underline boolean not null default false,
    unique(slide_id)
);

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'fk_projects_active_concept'
          and conrelid = 'public.projects'::regclass
    ) then
        alter table public.projects
            add constraint fk_projects_active_concept
            foreign key (active_concept_id)
            references public.concepts(id)
            on delete set null
            deferrable initially deferred;
    end if;
end $$;

create table if not exists public.app_logs (
    id bigserial primary key,
    created_at timestamptz not null default timezone('utc', now()),
    level varchar(32) not null,
    logger varchar(255) not null,
    message text not null,
    request_id varchar(64),
    method varchar(16),
    path text,
    remote_addr varchar(128),
    user_id bigint references public.users(id) on delete set null,
    status_code integer,
    duration_ms numeric,
    extra jsonb,
    stacktrace text
);

create index if not exists idx_app_logs_created_at on public.app_logs(created_at);
create index if not exists idx_app_logs_level on public.app_logs(level);
create index if not exists idx_app_logs_request_id on public.app_logs(request_id);

create table if not exists public.render_artifacts (
    id bigserial primary key,
    project_id bigint not null references public.projects(id) on delete cascade,
    concept_id bigint references public.concepts(id) on delete set null,
    user_id bigint references public.users(id) on delete set null,
    job_id varchar(64) not null unique,
    status varchar(32) not null default 'queued',
    voice varchar(50),
    download_type varchar(16),
    download_url text,
    storage_path text,
    file_size bigint,
    suggested_filename varchar(255),
    render_signature varchar(128),
    cache_hit boolean not null default false,
    error text,
    created_at timestamptz not null default timezone('utc', now()),
    updated_at timestamptz not null default timezone('utc', now()),
    completed_at timestamptz,
    download_count integer not null default 0
);

create index if not exists idx_render_artifacts_project on public.render_artifacts(project_id);
create index if not exists idx_render_artifacts_concept on public.render_artifacts(concept_id);
create index if not exists idx_render_artifacts_job on public.render_artifacts(job_id);
