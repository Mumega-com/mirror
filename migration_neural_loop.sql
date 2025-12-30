-- Phase 19: The Neural Loop Migration
-- Change embedding dimension from 1536 (OpenAI) to 384 (all-MiniLM-L6-v2)

-- 1. Drop the existing index (since dimensions change)
DROP INDEX IF EXISTS mumega_engrams_embedding_idx;

-- 2. Alter the column type
ALTER TABLE public.mumega_engrams 
ALTER COLUMN embedding TYPE vector(384);

-- 3. Re-create the index (IVFFlat for speed)
CREATE INDEX ON public.mumega_engrams USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- 4. Create the Vector Search RPC function
create or replace function match_engrams (
  query_embedding vector(384),
  match_threshold float,
  match_count int
)
returns table (
  id uuid,
  content text,
  similarity float
)
language plpgsql
as $$
begin
  return query
  select
    mumega_engrams.id,
    mumega_engrams.content,
    1 - (mumega_engrams.embedding <=> query_embedding) as similarity
  from mumega_engrams
  where 1 - (mumega_engrams.embedding <=> query_embedding) > match_threshold
  order by mumega_engrams.embedding <=> query_embedding
  limit match_count;
end;
$$;
