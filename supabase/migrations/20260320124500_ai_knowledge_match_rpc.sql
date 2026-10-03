create or replace function public.match_ai_knowledge_chunks(
  query_embedding vector(1536),
  match_count int default 5,
  filter jsonb default '{}'::jsonb
)
returns table (
  id uuid,
  source_type text,
  source_ref text,
  content text,
  metadata jsonb,
  similarity float
)
language sql
stable
as $$
  select
    chunk.id,
    chunk.source_type,
    chunk.source_ref,
    chunk.content,
    chunk.metadata,
    1 - (chunk.embedding <=> query_embedding) as similarity
  from public.ai_knowledge_chunks as chunk
  where
    case
      when filter = '{}'::jsonb then true
      else chunk.metadata @> filter
    end
  order by chunk.embedding <=> query_embedding
  limit greatest(1, match_count);
$$;
