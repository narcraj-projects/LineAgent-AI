-- Dedicated synthetic demo only. Run once in the Supabase SQL Editor.
-- Server-side service_role only: the Streamlit role selector is NOT authentication.
begin;

create table if not exists public.linemedic_catalog (
  namespace text primary key check (namespace = 'linemedic-synthetic-v1'),
  baseline_digest text not null,
  payload jsonb not null check (payload->>'synthetic' = 'true')
);
create table if not exists public.linemedic_work_orders (
  namespace text not null references public.linemedic_catalog(namespace),
  work_order_id text not null check (work_order_id like 'SYN-WO-%'),
  equipment_id text not null, alarm_code text not null, fix_id text not null,
  actor_id text not null, outcome text not null check (outcome in ('worked','did_not_work')),
  payload jsonb not null check (payload->>'synthetic' = 'true'),
  primary key(namespace, work_order_id)
);
create index if not exists linemedic_history_idx on public.linemedic_work_orders(namespace,equipment_id,fix_id);
create table if not exists public.linemedic_feedback (
  namespace text not null references public.linemedic_catalog(namespace),
  event_id uuid not null, work_order_id text not null,
  submitted_at timestamptz not null, payload jsonb not null,
  primary key(namespace,event_id), unique(namespace,work_order_id),
  foreign key(namespace,work_order_id) references public.linemedic_work_orders(namespace,work_order_id)
);
create table if not exists public.linemedic_outbox (
  namespace text not null, event_id uuid not null,
  state text not null default 'pending' check(state in ('pending','synced')),
  attempts integer not null default 0 check(attempts >= 0),
  last_error_code text, synced_at timestamptz,
  primary key(namespace,event_id),
  foreign key(namespace,event_id) references public.linemedic_feedback(namespace,event_id)
);

alter table public.linemedic_catalog enable row level security;
alter table public.linemedic_work_orders enable row level security;
alter table public.linemedic_feedback enable row level security;
alter table public.linemedic_outbox enable row level security;
revoke all on public.linemedic_catalog, public.linemedic_work_orders,
  public.linemedic_feedback, public.linemedic_outbox from anon, authenticated;
grant select,insert,update on public.linemedic_catalog, public.linemedic_work_orders,
  public.linemedic_feedback, public.linemedic_outbox to service_role;

create or replace function public.linemedic_seed(p_dataset jsonb, p_digest text)
returns jsonb language plpgsql security invoker set search_path=public,pg_temp as $$
declare w jsonb; existing text; ns constant text := 'linemedic-synthetic-v1';
begin
  if p_dataset->>'synthetic' is distinct from 'true'
     or p_dataset->>'schema_version' is distinct from '1.0'
     or jsonb_array_length(p_dataset->'work_orders') <> 50 then
    raise exception 'Invalid synthetic baseline';
  end if;
  insert into public.linemedic_catalog values(ns,p_digest,p_dataset) on conflict do nothing;
  select baseline_digest into existing from public.linemedic_catalog where namespace=ns for update;
  if existing <> p_digest or (select payload from public.linemedic_catalog where namespace=ns) <> p_dataset then
    raise exception 'Baseline conflict; migration required';
  end if;
  for w in select value from jsonb_array_elements(p_dataset->'work_orders') loop
    insert into public.linemedic_work_orders values(ns,w->>'work_order_id',w->>'equipment_id',w->>'alarm_code',
      w->>'fix_id',w->>'actor_id',w->>'outcome',w) on conflict do nothing;
    if (select payload from public.linemedic_work_orders where namespace=ns and work_order_id=w->>'work_order_id') <> w then
      raise exception 'Work order seed conflict';
    end if;
  end loop;
  return jsonb_build_object('baseline_work_orders',50);
end $$;

create or replace function public.linemedic_snapshot()
returns jsonb language sql stable security invoker set search_path=public,pg_temp as $$
  select jsonb_set(c.payload,'{work_orders}',
    coalesce((select jsonb_agg(w.payload order by w.work_order_id) from public.linemedic_work_orders w
      where w.namespace=c.namespace),'[]'::jsonb))
  from public.linemedic_catalog c where c.namespace='linemedic-synthetic-v1';
$$;

create or replace function public.linemedic_history(p_equipment_id text,p_fix_id text)
returns jsonb language sql stable security invoker set search_path=public,pg_temp as $$
  select coalesce(jsonb_agg(payload order by work_order_id),'[]'::jsonb)
  from public.linemedic_work_orders where namespace='linemedic-synthetic-v1'
    and equipment_id=p_equipment_id and fix_id=p_fix_id;
$$;

create or replace function public.linemedic_exact_sources(p_alarm_code text,p_equipment_id text)
returns jsonb language sql stable security invoker set search_path=public,pg_temp as $$
  with sources as (
    select distinct m->>'section_id' as id, 'manual' as kind, m->>'text' as body
    from public.linemedic_catalog c,
      lateral jsonb_array_elements(c.payload->'manual_sections') m,
      lateral jsonb_array_elements(c.payload->'alarms') a
    where c.namespace='linemedic-synthetic-v1' and a->>'failure_mode_id'=m->>'failure_mode_id'
      and m->'equipment_ids' ? (a->>'equipment_id')
      and (p_alarm_code is null or a->>'code'=p_alarm_code)
      and (p_equipment_id is null or a->>'equipment_id'=p_equipment_id)
    union all
    select work_order_id,'work_order',payload->>'note' from public.linemedic_work_orders
    where namespace='linemedic-synthetic-v1'
      and (p_alarm_code is null or alarm_code=p_alarm_code)
      and (p_equipment_id is null or equipment_id=p_equipment_id)
  ) select coalesce(jsonb_agg(jsonb_build_object('source_id',id,'source_kind',kind,
       'text',body,'provider','supabase:live','synthetic',true) order by id),'[]'::jsonb) from sources;
$$;

create or replace function public.linemedic_record_feedback(p_event jsonb)
returns jsonb language plpgsql security invoker set search_path=public,pg_temp as $$
declare
  ns constant text := 'linemedic-synthetic-v1'; catalog jsonb; w jsonb := p_event->'work_order';
  eid uuid := (p_event->>'event_id')::uuid; existing jsonb; a jsonb; fm jsonb;
begin
  select payload into catalog from public.linemedic_catalog where namespace=ns;
  if catalog is null then raise exception 'Seed the catalog first'; end if;
  if w->>'work_order_id' is distinct from 'SYN-WO-FB-' || replace(eid::text,'-','')
     or w->>'synthetic' is distinct from 'true'
     or w->>'outcome' not in ('worked','did_not_work')
     or w->>'note' is distinct from 'Synthetic demo feedback: ' || (w->>'outcome') || '.'
     or w->>'date' is distinct from to_char((p_event->>'submitted_at')::timestamptz at time zone 'UTC','YYYY-MM-DD') then
    raise exception 'Invalid synthetic feedback';
  end if;
  if not exists(select 1 from jsonb_array_elements(catalog->'demo_identities') actor
      where actor->>'actor_id'=w->>'actor_id' and actor->>'role'='technician') then
    raise exception 'Technician demo actor required';
  end if;
  select value into a from jsonb_array_elements(catalog->'alarms') where value->>'code'=w->>'alarm_code';
  select value into fm from jsonb_array_elements(catalog->'failure_modes')
    where value->>'failure_mode_id'=a->>'failure_mode_id';
  if a is null or a->>'equipment_id' is distinct from w->>'equipment_id'
     or not coalesce(fm->'fix_ids' ? (w->>'fix_id'),false) then
    raise exception 'Feedback context mismatch';
  end if;
  -- All three inserts are one PostgreSQL transaction. A retry never increments history twice.
  insert into public.linemedic_work_orders values(ns,w->>'work_order_id',w->>'equipment_id',w->>'alarm_code',
      w->>'fix_id',w->>'actor_id',w->>'outcome',w) on conflict do nothing;
  if (select payload from public.linemedic_work_orders where namespace=ns and work_order_id=w->>'work_order_id') <> w then
    raise exception 'Work order event conflict';
  end if;
  insert into public.linemedic_feedback values(ns,eid,w->>'work_order_id',(p_event->>'submitted_at')::timestamptz,p_event)
      on conflict do nothing;
  select payload into existing from public.linemedic_feedback where namespace=ns and event_id=eid;
  if existing <> p_event then raise exception 'Idempotency event conflict'; end if;
  insert into public.linemedic_outbox(namespace,event_id) values(ns,eid) on conflict do nothing;
  return (select jsonb_build_object('event',p_event,'sync_state',state,'attempts',attempts)
      from public.linemedic_outbox where namespace=ns and event_id=eid);
end $$;

create or replace function public.linemedic_pending_feedback()
returns jsonb language sql stable security invoker set search_path=public,pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('event',f.payload,'sync_state',o.state,'attempts',o.attempts)
    order by f.submitted_at,f.event_id),'[]'::jsonb)
  from public.linemedic_feedback f join public.linemedic_outbox o using(namespace,event_id)
  where o.namespace='linemedic-synthetic-v1' and o.state='pending';
$$;

create or replace function public.linemedic_ack_sync(p_event_id uuid,p_success boolean)
returns jsonb language plpgsql security invoker set search_path=public,pg_temp as $$
declare receipt jsonb;
begin
  update public.linemedic_outbox set attempts=attempts+1,
    state=case when p_success then 'synced' else 'pending' end,
    last_error_code=case when p_success then null else 'graph_sync_failed' end,
    synced_at=case when p_success then now() else null end
  where namespace='linemedic-synthetic-v1' and event_id=p_event_id and state='pending';
  select jsonb_build_object('event',f.payload,'sync_state',o.state,'attempts',o.attempts) into receipt
  from public.linemedic_feedback f join public.linemedic_outbox o using(namespace,event_id)
  where f.namespace='linemedic-synthetic-v1' and f.event_id=p_event_id;
  if receipt is null then raise exception 'Unknown feedback event'; end if;
  return receipt;
end $$;

revoke all on function public.linemedic_seed(jsonb,text), public.linemedic_snapshot(),
 public.linemedic_history(text,text), public.linemedic_exact_sources(text,text),
 public.linemedic_record_feedback(jsonb), public.linemedic_pending_feedback(),
 public.linemedic_ack_sync(uuid,boolean) from public, anon, authenticated;
grant execute on function public.linemedic_seed(jsonb,text), public.linemedic_snapshot(),
 public.linemedic_history(text,text), public.linemedic_exact_sources(text,text),
 public.linemedic_record_feedback(jsonb), public.linemedic_pending_feedback(),
 public.linemedic_ack_sync(uuid,boolean) to service_role;
notify pgrst, 'reload schema';
commit;
