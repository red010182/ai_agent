const BASE = '/api'

// ── Session 管理 ──────────────────────────────────────────────────────────────

export async function createSession() {
  const res = await fetch(`${BASE}/sessions`, { method: 'POST' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<{ session_id: string; created_at: string }>
}

export async function deleteSession(sessionId: string) {
  await fetch(`${BASE}/sessions/${sessionId}`, { method: 'DELETE' })
}

// ── SSE 事件型別 ──────────────────────────────────────────────────────────────

export type SseEvent =
  | { type: 'text_delta'; content: string }
  | { type: 'sql_confirm'; sql: string; reply: string }
  | { type: 'ask_user'; reply: string }
  | { type: 'collect_params'; params: string[] }
  | { type: 'trace_routing'; matched_sop: string | null; matched_case: string | null; case_title: string | null; score: number; mode: 'sop' | 'fallback_chat' }
  | { type: 'trace_case'; case_id: string; case_title: string; scenario: string; step: string }
  | { type: 'trace_facts'; known_facts: string[] }
  | { type: 'trace_sql'; template: string; filled: string; result_rows: number; result_preview: Record<string, unknown>[] }
  | { type: 'trace_decision'; candidates: Array<{ case_id: string; symptom: string }>; chosen: string | null; reason: string }
  | { type: 'sql_error'; error_message: string; sql: string; hint: string }
  | { type: 'select_case'; candidates: Array<{ case_id: string; title: string; symptom: string }>; reply: string }
  | { type: 'clarify'; reply: string; options: string[] }
  | { type: 'done' }
  | { type: 'error'; message: string }

// ── SSE 串流（POST + ReadableStream）─────────────────────────────────────────

export async function* streamChat(
  sessionId: string,
  message: string,
): AsyncGenerator<SseEvent> {
  const res = await fetch(`${BASE}/sessions/${sessionId}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
  })
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const raw = line.slice(6).trim()
        if (!raw) continue
        try { yield JSON.parse(raw) as SseEvent } catch { /* skip malformed */ }
      }
    }
  }
}
