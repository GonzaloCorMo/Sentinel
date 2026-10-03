-- Telemetria historica + protocolos RAG + propuestas HITL
-- (complementa migraciones anteriores: vector extension ya creada en 20260320123000)

CREATE TABLE IF NOT EXISTS public.telemetry_logs (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ambulance_id text        NOT NULL,
  timestamp    timestamptz NOT NULL DEFAULT now(),
  medical_data jsonb,
  mechanical_data jsonb,
  gps_data     jsonb
);

CREATE INDEX IF NOT EXISTS telemetry_logs_amb_ts_idx
  ON public.telemetry_logs (ambulance_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS public.protocols_knowledge (
  id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  category  text NOT NULL,
  content   text NOT NULL,
  embedding vector(1536)
);

CREATE INDEX IF NOT EXISTS protocols_knowledge_embedding_idx
  ON public.protocols_knowledge
  USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

CREATE TABLE IF NOT EXISTS public.ai_hitl_proposals (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ambulance_id             text NOT NULL,
  anomaly_type             text NOT NULL,
  anomaly_detail           jsonb NOT NULL DEFAULT '{}'::jsonb,
  matched_protocol_id      uuid REFERENCES public.protocols_knowledge(id) ON DELETE SET NULL,
  matched_protocol_content text,
  similarity               float,
  status                   text NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'approved', 'rejected')),
  created_at               timestamptz NOT NULL DEFAULT now(),
  resolved_at              timestamptz
);

CREATE INDEX IF NOT EXISTS ai_hitl_proposals_status_idx
  ON public.ai_hitl_proposals (status)
  WHERE status = 'pending';

CREATE OR REPLACE FUNCTION public.match_protocols(
  query_embedding vector(1536),
  match_threshold float DEFAULT 0.0,
  match_count     int   DEFAULT 3
)
RETURNS TABLE (
  id         uuid,
  category   text,
  content    text,
  similarity float
)
LANGUAGE sql STABLE
AS $$
  SELECT
    p.id,
    p.category,
    p.content,
    1 - (p.embedding <=> query_embedding) AS similarity
  FROM public.protocols_knowledge AS p
  WHERE p.embedding IS NOT NULL
    AND 1 - (p.embedding <=> query_embedding) >= match_threshold
  ORDER BY p.embedding <=> query_embedding
  LIMIT greatest(1, match_count);
$$;
