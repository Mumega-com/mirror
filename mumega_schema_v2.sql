-- Mumega Schema V2 (Torivers Fusion)
-- Combines FRC Engine (Soul Prints) with Marketplace (Billing, Products)

-- ==========================================
-- 1. CORE PROFILES (From Torivers `profiles`)
-- ==========================================
-- Extends Supabase Auth
create table if not exists public.mumega_profiles (
  id uuid references auth.users on delete cascade primary key,
  email text,
  full_name text,
  username text unique,
  wallet_balance decimal(10,2) default 0.00, -- Torivers Core Feature
  total_spent decimal(10,2) default 0.00,
  subscription_tier text default 'Free',
  created_at timestamp with time zone default now()
);

alter table public.mumega_profiles enable row level security;
create policy "Users can view own profile" on public.mumega_profiles for select using (auth.uid() = id);

-- ==========================================
-- 2. MARKETPLACE PRODUCTS (From Torivers `automations`)
-- Mapped to "Archetypes" in Mumega
-- ==========================================
create table if not exists public.mumega_archetypes (
  id uuid default gen_random_uuid() primary key,
  title text not null, -- e.g. "The Guardian"
  description text not null,
  creator_id uuid references public.mumega_profiles(id),
  
  -- Price & Stats
  base_price decimal(8,2) not null, -- Cost to "Spark" (Mint)
  total_installs integer default 0,
  average_rating decimal(3,2) default 0,
  
  -- FRC Configuration (The "DNA")
  base_soul_print jsonb not null default '{}'::jsonb, -- Base 16D weights
  initial_vortex_config jsonb, -- Default Logo/Mythos balance
  
  is_published boolean default false,
  created_at timestamp with time zone default now()
);

-- RLS
alter table public.mumega_archetypes enable row level security;
create policy "Public view published archetypes" on public.mumega_archetypes for select using (is_published = true);

-- ==========================================
-- 3. LIVING CHARACTERS (From Torivers `user_automations`)
-- Mapped to "Characters" in Mumega
-- ==========================================
create table if not exists public.mumega_characters (
  id uuid default gen_random_uuid() primary key,
  user_id uuid references public.mumega_profiles(id) on delete cascade not null,
  archetype_id uuid references public.mumega_archetypes(id), -- The source template
  
  -- Identity
  name text not null,
  soul_print jsonb not null default '{}'::jsonb, -- THE LIVE STATE (Evolving)
  
  -- Q-NFT Status
  is_minted boolean default false,
  nft_contract_address text,
  nft_token_id text,
  
  -- Torivers Operational Fields
  is_active boolean default true,
  last_interaction_at timestamp with time zone,
  
  created_at timestamp with time zone default now(),
  updated_at timestamp with time zone default now()
);

alter table public.mumega_characters enable row level security;
create policy "Users manage own characters" on public.mumega_characters for all using (auth.uid() = user_id);

-- ==========================================
-- 4. FRC MEMORY (Legacy Mumega `engrams`)
-- ==========================================
create table if not exists public.mumega_engrams (
  id uuid default gen_random_uuid() primary key,
  character_id uuid references public.mumega_characters(id) on delete cascade not null,
  content text not null,
  embedding vector(1536), 
  kernel_16d vector(16), 
  timestamp timestamp with time zone default now()
);

create index on public.mumega_engrams using ivfflat (embedding vector_cosine_ops) with (lists = 100);
alter table public.mumega_engrams enable row level security;
create policy "Users access character memory" on public.mumega_engrams for all using (
  exists (select 1 from public.mumega_characters c where c.id = character_id and c.user_id = auth.uid())
);

-- ==========================================
-- 5. BILLING & WALLET (From Torivers `wallet_transactions`)
-- ==========================================
create table public.mumega_wallet_transactions (
  id uuid default gen_random_uuid() primary key,
  user_id uuid references public.mumega_profiles(id) on delete cascade,
  
  type text check (type in ('credit', 'debit', 'mint_fee')),
  amount decimal(10,2) not null,
  balance_after decimal(10,2) not null,
  description text not null,
  
  character_id uuid references public.mumega_characters(id), -- If related to a specific char
  created_at timestamp with time zone default now()
);

alter table public.mumega_wallet_transactions enable row level security;
create policy "Users view own wallet" on public.mumega_wallet_transactions for select using (auth.uid() = user_id);

-- ==========================================
-- 6. FUNCTIONS (From Torivers)
-- ==========================================
create or replace function update_mumega_wallet_balance()
returns trigger
language plpgsql
security definer
as $$
begin
  update public.mumega_profiles
  set wallet_balance = new.balance_after
  where id = new.user_id;
  return new;
end;
$$;

create trigger tr_update_wallet
  after insert on public.mumega_wallet_transactions
  for each row execute function update_mumega_wallet_balance();
