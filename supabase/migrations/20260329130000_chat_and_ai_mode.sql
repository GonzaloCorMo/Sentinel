-- Chat messages for chatbot RAG conversations
CREATE TABLE IF NOT EXISTS public.chat_messages (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id text NOT NULL,
  role       text NOT NULL CHECK (role IN ('user', 'assistant')),
  content    text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chat_messages_session_idx
  ON public.chat_messages (session_id, created_at);
