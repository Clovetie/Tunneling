-- FOUNDRY — Visual Multi-Agent Development Studio
-- Apply in Supabase SQL editor or with `npm run migrate`.
-- All browser-readable tables have owner_id and RLS. Service-only tables intentionally have no anonymous policy.

create extension if not exists pgcrypto;
create extension if not exists vector;

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text,
  display_name text,
  created_at timestamptz not null default now()
);

create table if not exists public.projects (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  slug text not null,
  description text not null default '',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_id, slug)
);

create table if not exists public.project_members (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null default 'viewer' check (role in ('owner','editor','viewer')),
  created_at timestamptz not null default now(),
  unique (project_id, user_id)
);

create or replace function public.foundry_project_access(p_project uuid, p_min text default 'viewer')
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(auth.uid(), '00000000-0000-0000-0000-000000000000'::uuid) <> '00000000-0000-0000-0000-000000000000'::uuid
    and (
      exists (select 1 from public.projects p where p.id = p_project and p.owner_id = auth.uid())
      or exists (
        select 1 from public.project_members pm
        where pm.project_id = p_project and pm.user_id = auth.uid()
          and case p_min
            when 'owner' then pm.role = 'owner'
            when 'editor' then pm.role in ('owner','editor')
            else true
          end
      )
    );
$$;

create table if not exists public.agents (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  project_id uuid references public.projects(id) on delete set null,
  name text not null,
  slug text not null,
  description text not null default '',
  avatar text not null default 'bot',
  identity jsonb not null default '{}'::jsonb,
  system_instructions text not null default '',
  model jsonb not null default '{"provider":"openai","model":"gpt-5-mini"}'::jsonb,
  generation jsonb not null default '{"temperature":0.2,"maxTokens":4096}'::jsonb,
  tool_permissions jsonb not null default '[]'::jsonb,
  memory_config jsonb not null default '{"enabled":true,"scope":"agent","retrievalLimit":6}'::jsonb,
  knowledge_sources jsonb not null default '[]'::jsonb,
  communication_permissions jsonb not null default '{"direct":true,"broadcast":false,"task":true,"artifact":true,"handoff":true}'::jsonb,
  execution_limits jsonb not null default '{"maxParallelTasks":1,"maxTokensPerRun":30000,"maxToolCalls":20,"timeoutMs":120000}'::jsonb,
  assigned_projects jsonb not null default '[]'::jsonb,
  status text not null default 'idle' check (status in ('idle','thinking','executing','waiting_approval','blocked','failed','completed','disabled')),
  enabled boolean not null default true,
  is_template boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_id, slug)
);

create table if not exists public.agent_templates (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid references auth.users(id) on delete cascade,
  slug text not null,
  name text not null,
  description text not null default '',
  category text not null default 'general',
  is_roblox boolean not null default false,
  config jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create unique index if not exists agent_templates_owner_slug_uidx
  on public.agent_templates (coalesce(owner_id, '00000000-0000-0000-0000-000000000000'::uuid), slug);

create table if not exists public.workflows (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  project_id uuid references public.projects(id) on delete set null,
  name text not null,
  slug text not null,
  description text not null default '',
  graph jsonb not null default '{"nodes":[],"edges":[]}'::jsonb,
  is_template boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_id, slug)
);

create table if not exists public.workflow_runs (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  workflow_id uuid references public.workflows(id) on delete set null,
  project_id uuid references public.projects(id) on delete set null,
  objective text,
  mode text not null default 'graph' check (mode in ('graph','objective')),
  status text not null default 'queued' check (status in ('queued','planning','running','waiting_approval','blocked','failed','completed','cancelled')),
  config jsonb not null default '{}'::jsonb,
  input jsonb not null default '{}'::jsonb,
  output jsonb,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.tasks (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  run_id uuid not null references public.workflow_runs(id) on delete cascade,
  project_id uuid references public.projects(id) on delete set null,
  key text not null,
  node_id text not null default '',
  kind text not null,
  status text not null default 'todo' check (status in ('todo','running','waiting_approval','blocked','failed','completed','skipped','cancelled')),
  depends_on jsonb not null default '[]'::jsonb,
  branch_conditions jsonb not null default '[]'::jsonb,
  assignee_agent_id uuid references public.agents(id) on delete set null,
  input jsonb not null default '{}'::jsonb,
  output jsonb,
  artifacts jsonb,
  attempts integer not null default 0,
  max_attempts integer not null default 2,
  timeout_ms integer not null default 120000,
  resource_lock text,
  lease_owner text,
  lease_expires_at timestamptz,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (run_id, key)
);

create table if not exists public.artifacts (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  project_id uuid references public.projects(id) on delete set null,
  run_id uuid references public.workflow_runs(id) on delete cascade,
  task_id uuid references public.tasks(id) on delete set null,
  name text not null,
  kind text not null default 'json',
  uri text,
  metadata jsonb not null default '{}'::jsonb,
  content jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.messages (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  project_id uuid references public.projects(id) on delete set null,
  run_id uuid references public.workflow_runs(id) on delete cascade,
  task_id uuid references public.tasks(id) on delete set null,
  sender_agent_id uuid references public.agents(id) on delete set null,
  recipient_agent_id uuid references public.agents(id) on delete set null,
  channel text not null default 'task' check (channel in ('direct','broadcast','task','artifact','handoff','system')),
  transport text not null default 'foundry' check (transport in ('foundry','tunneling_bus','external')),
  content text not null default '',
  payload jsonb not null default '{}'::jsonb,
  artifact_id uuid references public.artifacts(id) on delete set null,
  external_id text,
  created_at timestamptz not null default now(),
  unique (transport, external_id)
);

create table if not exists public.events (
  id bigserial primary key,
  event_id uuid not null default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  project_id uuid references public.projects(id) on delete set null,
  run_id uuid references public.workflow_runs(id) on delete cascade,
  task_id uuid references public.tasks(id) on delete set null,
  agent_id uuid references public.agents(id) on delete set null,
  kind text not null,
  level text not null default 'info' check (level in ('debug','info','warn','error')),
  data jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.memories (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  scope text not null check (scope in ('agent','project','team','workflow')),
  agent_id uuid references public.agents(id) on delete cascade,
  project_id uuid references public.projects(id) on delete cascade,
  run_id uuid references public.workflow_runs(id) on delete cascade,
  team_id text,
  title text not null,
  content text not null,
  visibility text not null default 'private' check (visibility in ('private','team','project')),
  embedding vector(1536),
  metadata jsonb not null default '{}'::jsonb,
  version integer not null default 1,
  created_by text not null default 'user',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.memory_versions (
  id uuid primary key default gen_random_uuid(),
  memory_id uuid not null references public.memories(id) on delete cascade,
  owner_id uuid not null references auth.users(id) on delete cascade,
  version integer not null,
  title text not null,
  content text not null,
  metadata jsonb not null default '{}'::jsonb,
  change_note text not null default '',
  created_at timestamptz not null default now(),
  unique (memory_id, version)
);

create table if not exists public.knowledge_documents (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  project_id uuid references public.projects(id) on delete cascade,
  title text not null,
  source_uri text,
  mime_type text not null default 'text/plain',
  metadata jsonb not null default '{}'::jsonb,
  content text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.knowledge_chunks (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  document_id uuid not null references public.knowledge_documents(id) on delete cascade,
  project_id uuid references public.projects(id) on delete cascade,
  chunk_index integer not null,
  content text not null,
  embedding vector(1536),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (document_id, chunk_index)
);

create table if not exists public.integrations (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  kind text not null,
  name text not null,
  transport text not null default 'streamable_http' check (transport in ('streamable_http','stdio','http_api')),
  base_url text,
  config jsonb not null default '{}'::jsonb,
  status text not null default 'unconfigured' check (status in ('unconfigured','connected','degraded','disconnected','error')),
  last_checked_at timestamptz,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_id, kind, name)
);

create table if not exists public.integration_secrets (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  integration_id uuid not null references public.integrations(id) on delete cascade,
  name text not null,
  ciphertext text not null,
  iv text not null,
  auth_tag text not null,
  key_version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (integration_id, name)
);

create table if not exists public.tools (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  integration_id uuid references public.integrations(id) on delete cascade,
  name text not null,
  kind text not null default 'mcp' check (kind in ('mcp','http','file','code','search','database','custom','knowledge','memory')),
  description text not null default '',
  input_schema jsonb not null default '{}'::jsonb,
  output_schema jsonb,
  required_permissions jsonb not null default '["read"]'::jsonb,
  timeout_ms integer not null default 30000,
  availability text not null default 'unknown' check (availability in ('unknown','available','degraded','unavailable','approval_required')),
  metadata jsonb not null default '{}'::jsonb,
  last_seen_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_id, integration_id, name)
);

create table if not exists public.approvals (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  run_id uuid references public.workflow_runs(id) on delete cascade,
  task_id uuid references public.tasks(id) on delete cascade,
  tool_id uuid references public.tools(id) on delete set null,
  kind text not null check (kind in ('human_node','destructive_tool','publish','external_communication','credential','project_wide','bridge_confirmation')),
  status text not null default 'pending' check (status in ('pending','approved','denied','expired')),
  reason text not null default '',
  payload jsonb not null default '{}'::jsonb,
  requested_by text not null default 'system',
  decided_by uuid references auth.users(id) on delete set null,
  decision_note text,
  created_at timestamptz not null default now(),
  decided_at timestamptz
);

create table if not exists public.workspace_items (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  project_id uuid references public.projects(id) on delete cascade,
  kind text not null default 'note' check (kind in ('note','task','conversation','prompt','output','attachment','workflow_template','saved_output')),
  title text not null,
  content text not null default '',
  status text not null default 'open' check (status in ('open','active','done','archived')),
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Service-only tables: the browser never queries these directly. The service role bypasses RLS.
create table if not exists public.audit_logs (
  id bigserial primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  action text not null,
  actor text not null default 'user',
  target text not null default '',
  outcome text not null default 'ok',
  detail jsonb not null default '{}'::jsonb,
  prev_hash text not null default repeat('0', 64),
  hash text not null,
  created_at timestamptz not null default now()
);

create table if not exists public.rate_limits (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  action_key text not null,
  window_start timestamptz not null,
  count integer not null default 0,
  updated_at timestamptz not null default now(),
  unique (owner_id, action_key, window_start)
);

create table if not exists public.durable_jobs (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  type text not null check (type in ('run.workflow','run.coordinate','task.execute','tool.call','approval.continue','mcp.refresh')),
  payload jsonb not null default '{}'::jsonb,
  status text not null default 'queued' check (status in ('queued','running','done','failed','cancelled')),
  attempts integer not null default 0,
  max_attempts integer not null default 3,
  lease_owner text,
  lease_expires_at timestamptz,
  available_at timestamptz not null default now(),
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.execution_workers (
  id text primary key,
  status text not null default 'starting' check (status in ('starting','online','draining','offline')),
  last_heartbeat timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb,
  started_at timestamptz not null default now()
);

-- Indexes
create index if not exists projects_owner_idx on public.projects(owner_id);
create index if not exists agents_owner_status_idx on public.agents(owner_id,status);
create index if not exists workflow_runs_owner_status_idx on public.workflow_runs(owner_id,status,created_at desc);
create index if not exists tasks_run_status_idx on public.tasks(run_id,status);
create index if not exists tasks_lease_idx on public.tasks(status,lease_expires_at);
create index if not exists events_owner_cursor_idx on public.events(owner_id,id desc);
create index if not exists messages_run_idx on public.messages(run_id,created_at);
create index if not exists memories_scope_agent_idx on public.memories(owner_id,scope,agent_id);
create index if not exists memories_scope_project_idx on public.memories(owner_id,scope,project_id);
create index if not exists knowledge_chunks_doc_idx on public.knowledge_chunks(document_id,chunk_index);
create index if not exists tools_integration_idx on public.tools(owner_id,integration_id);
create index if not exists approvals_status_idx on public.approvals(owner_id,status,created_at desc);
create index if not exists durable_jobs_claim_idx on public.durable_jobs(status,available_at,created_at);
create index if not exists workspace_items_project_idx on public.workspace_items(owner_id,project_id,created_at desc);
create index if not exists audit_logs_owner_id_idx on public.audit_logs(owner_id,id);

-- pgvector approximate indexes. Supabase's vector extension supports ivfflat.
create index if not exists memories_embedding_idx on public.memories using ivfflat (embedding vector_cosine_ops) with (lists = 100);
create index if not exists knowledge_chunks_embedding_idx on public.knowledge_chunks using ivfflat (embedding vector_cosine_ops) with (lists = 100);

-- updated_at trigger
create or replace function public.foundry_touch_updated_at()
returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end $$;

do $$
declare t text;
begin
  foreach t in array array[
    'projects','agents','workflows','workflow_runs','tasks','memories','knowledge_documents',
    'integrations','integration_secrets','tools','workspace_items','durable_jobs'
  ] loop
    execute format('drop trigger if exists %I on public.%I', 'foundry_touch_' || t, t);
    execute format('create trigger %I before update on public.%I for each row execute function public.foundry_touch_updated_at()', 'foundry_touch_' || t, t);
  end loop;
end $$;

-- Memory version snapshots. API edits content; trigger stores the prior immutable version.
create or replace function public.foundry_snapshot_memory()
returns trigger language plpgsql as $$
begin
  if (new.title is distinct from old.title or new.content is distinct from old.content or new.metadata is distinct from old.metadata or new.embedding is distinct from old.embedding) then
    insert into public.memory_versions (memory_id, owner_id, version, title, content, metadata, change_note)
    values (old.id, old.owner_id, old.version, old.title, old.content, old.metadata, coalesce(new.metadata->>'changeNote',''))
    on conflict (memory_id, version) do nothing;
    new.version := old.version + 1;
    new.updated_at := now();
  end if;
  return new;
end $$;

drop trigger if exists foundry_memory_version on public.memories;
create trigger foundry_memory_version before update on public.memories
for each row execute function public.foundry_snapshot_memory();

-- Semantic search functions. RLS still applies to the underlying tables.
create or replace function public.match_knowledge_chunks(
  query_embedding vector(1536),
  match_count integer default 8,
  p_owner uuid default auth.uid(),
  p_project uuid default null,
  p_document uuid default null
)
returns table (id uuid, document_id uuid, project_id uuid, title text, chunk_index integer, content text, similarity double precision, metadata jsonb)
language sql stable as $$
  select kc.id, kc.document_id, kc.project_id, kd.title, kc.chunk_index, kc.content,
    1 - (kc.embedding <=> query_embedding) as similarity, kc.metadata
  from public.knowledge_chunks kc
  join public.knowledge_documents kd on kd.id = kc.document_id
  where kc.owner_id = p_owner
    and kc.embedding is not null
    and (p_project is null or kc.project_id = p_project)
    and (p_document is null or kc.document_id = p_document)
  order by kc.embedding <=> query_embedding
  limit greatest(1, least(match_count, 50));
$$;

create or replace function public.match_memories(
  query_embedding vector(1536),
  match_count integer default 8,
  p_owner uuid default auth.uid(),
  p_scope text default null,
  p_agent uuid default null,
  p_project uuid default null,
  p_run uuid default null,
  p_team text default null
)
returns table (id uuid, scope text, agent_id uuid, project_id uuid, run_id uuid, title text, content text, version integer, similarity double precision, metadata jsonb)
language sql stable as $$
  select m.id, m.scope, m.agent_id, m.project_id, m.run_id, m.title, m.content, m.version,
    1 - (m.embedding <=> query_embedding) as similarity, m.metadata
  from public.memories m
  where m.owner_id = p_owner
    and m.embedding is not null
    and (p_scope is null or m.scope = p_scope)
    and (p_agent is null or m.agent_id = p_agent)
    and (p_project is null or m.project_id = p_project)
    and (p_run is null or m.run_id = p_run)
    and (p_team is null or m.team_id = p_team)
    and (p_scope is distinct from 'agent' or m.visibility = 'private' or m.metadata->>'explicitShare' = 'true')
    and (p_scope is distinct from 'team' or m.visibility in ('team','project'))
    and (p_scope is distinct from 'project' or m.visibility in ('private','project','team'))
  order by m.embedding <=> query_embedding
  limit greatest(1, least(match_count, 50));
$$;

-- Row Level Security
alter table public.profiles enable row level security;
alter table public.projects enable row level security;
alter table public.project_members enable row level security;
alter table public.agents enable row level security;
alter table public.agent_templates enable row level security;
alter table public.workflows enable row level security;
alter table public.workflow_runs enable row level security;
alter table public.tasks enable row level security;
alter table public.artifacts enable row level security;
alter table public.messages enable row level security;
alter table public.events enable row level security;
alter table public.memories enable row level security;
alter table public.memory_versions enable row level security;
alter table public.knowledge_documents enable row level security;
alter table public.knowledge_chunks enable row level security;
alter table public.integrations enable row level security;
alter table public.integration_secrets enable row level security;
alter table public.tools enable row level security;
alter table public.approvals enable row level security;
alter table public.workspace_items enable row level security;
alter table public.audit_logs enable row level security;
alter table public.rate_limits enable row level security;
alter table public.durable_jobs enable row level security;
alter table public.execution_workers enable row level security;

do $$
declare t text;
begin
  foreach t in array array[
    'profiles','projects','project_members','agents','agent_templates','workflows','workflow_runs','tasks',
    'artifacts','messages','events','memories','memory_versions','knowledge_documents','knowledge_chunks',
    'integrations','integration_secrets','tools','approvals','workspace_items','audit_logs','rate_limits',
    'durable_jobs','execution_workers'
  ] loop
    execute format('drop policy if exists %I on public.%I', 'foundry_owner_' || t, t);
  end loop;
end $$;

create policy foundry_owner_profiles on public.profiles for all using (id = auth.uid()) with check (id = auth.uid());
create policy foundry_owner_projects on public.projects for all using (owner_id = auth.uid()) with check (owner_id = auth.uid());

create policy foundry_owner_project_members on public.project_members for all
  using (user_id = auth.uid() or exists (select 1 from public.projects p where p.id = project_id and p.owner_id = auth.uid()))
  with check (exists (select 1 from public.projects p where p.id = project_id and p.owner_id = auth.uid()));

create policy foundry_owner_agents on public.agents for all using (owner_id = auth.uid()) with check (owner_id = auth.uid());
create policy foundry_owner_templates on public.agent_templates for all using (owner_id is null or owner_id = auth.uid()) with check (owner_id = auth.uid());
create policy foundry_owner_workflows on public.workflows for all using (owner_id = auth.uid() or foundry_project_access(project_id,'viewer')) with check (owner_id = auth.uid() or foundry_project_access(project_id,'editor'));
create policy foundry_owner_workflow_runs on public.workflow_runs for all using (owner_id = auth.uid() or foundry_project_access(project_id,'viewer')) with check (owner_id = auth.uid() or foundry_project_access(project_id,'editor'));
create policy foundry_owner_tasks on public.tasks for all using (owner_id = auth.uid() or foundry_project_access(project_id,'viewer')) with check (owner_id = auth.uid() or foundry_project_access(project_id,'editor'));
create policy foundry_owner_artifacts on public.artifacts for all using (owner_id = auth.uid() or foundry_project_access(project_id,'viewer')) with check (owner_id = auth.uid() or foundry_project_access(project_id,'editor'));
create policy foundry_owner_messages on public.messages for all using (owner_id = auth.uid() or foundry_project_access(project_id,'viewer')) with check (owner_id = auth.uid() or foundry_project_access(project_id,'editor'));
create policy foundry_owner_events on public.events for all using (owner_id = auth.uid() or foundry_project_access(project_id,'viewer')) with check (owner_id = auth.uid() or foundry_project_access(project_id,'editor'));
create policy foundry_owner_memories on public.memories for all using (owner_id = auth.uid()) with check (owner_id = auth.uid());
create policy foundry_owner_memory_versions on public.memory_versions for all using (owner_id = auth.uid()) with check (owner_id = auth.uid());
create policy foundry_owner_knowledge_documents on public.knowledge_documents for all using (owner_id = auth.uid() or foundry_project_access(project_id,'viewer')) with check (owner_id = auth.uid() or foundry_project_access(project_id,'editor'));
create policy foundry_owner_knowledge_chunks on public.knowledge_chunks for all using (owner_id = auth.uid() or foundry_project_access(project_id,'viewer')) with check (owner_id = auth.uid() or foundry_project_access(project_id,'editor'));
create policy foundry_owner_integrations on public.integrations for all using (owner_id = auth.uid()) with check (owner_id = auth.uid());
create policy foundry_owner_tools on public.tools for all using (owner_id = auth.uid()) with check (owner_id = auth.uid());
create policy foundry_owner_approvals on public.approvals for all using (owner_id = auth.uid()) with check (owner_id = auth.uid());
create policy foundry_owner_workspace_items on public.workspace_items for all using (owner_id = auth.uid() or foundry_project_access(project_id,'viewer')) with check (owner_id = auth.uid() or foundry_project_access(project_id,'editor'));

-- No policies on service-only tables: authenticated browser clients cannot read secrets, jobs, workers, audit or rate limits directly.
-- The service role bypasses RLS; all FOUNDRY API routes additionally filter by the authenticated owner id.

-- Supabase Realtime publication for live execution views.
do $$
begin
  if not exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    create publication supabase_realtime;
  end if;
end $$;

do $$
declare t text;
begin
  foreach t in array array['events','tasks','workflow_runs','agents','messages','approvals','tools','workspace_items'] loop
    execute format('alter table public.%I replica identity full', t);
    if not exists (
      select 1 from pg_publication_tables where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = t
    ) then
      execute format('alter publication supabase_realtime add table public.%I', t);
    end if;
  end loop;
end $$;
