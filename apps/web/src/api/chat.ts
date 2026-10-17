export type Author = { id: string; kind: 'owner' | 'miko' | 'contact'; name: string };
export type Message = { id: string; author: Author; source: 'web' | 'qq'; text: string;
  status: 'running' | 'completed' | 'cancelled' | 'error' | 'interrupted';
  error: string | null; created_at: number };
export type Conversation = { id: string; title: string; source: string;
  created_at: number; updated_at: number; preview: string | null };
export type ConversationDetail = Omit<Conversation, 'preview'> & { messages: Message[]; active_turn_id: string | null };
export type ChatEvent = { type: string; text?: string; status?: Message['status']; error?: string; warning?: string;
  turn_id?: string; conversation_id?: string; user_message_id?: string };

export async function responseError(response: Response) {
  const body = await response.json().catch(() => ({}));
  return typeof body.detail === 'string' ? body.detail : `请求失败（${response.status}）`;
}

export async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(await responseError(response));
  return response.json() as Promise<T>;
}

export async function* chatEvents(message: string, conversationId: string | null, signal: AbortSignal) {
  const response = await fetch('/api/chat', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, conversation_id: conversationId }), signal,
  });
  if (!response.ok) throw new Error(await responseError(response));
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let pending = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      pending += decoder.decode(value, { stream: !done });
      let boundary: number;
      while ((boundary = pending.indexOf('\n\n')) !== -1) {
        const block = pending.slice(0, boundary);
        pending = pending.slice(boundary + 2);
        const line = block.split('\n').find(item => item.startsWith('data:'));
        if (line) yield JSON.parse(line.slice(5)) as ChatEvent;
      }
      if (done) break;
    }
  } finally { reader.releaseLock(); }
}
