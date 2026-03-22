// ── Trace 事件資料型別 ────────────────────────────────────────────────────────

export interface TraceRouting {
  matched_sop: string | null
  matched_case: string | null
  case_title: string | null
  score: number
  mode: 'sop' | 'fallback_chat'
}

export interface TraceCase {
  case_id: string
  case_title: string
  scenario: string
  step: string
}

export interface TraceSql {
  template: string
  filled: string
  result_rows: number
  result_preview: Record<string, unknown>[]
}

export interface TraceDecision {
  candidates: Array<{ case_id: string; symptom: string }>
  chosen: string | null
  reason: string
}

export interface ThinkingData {
  routing?: TraceRouting
  cases: TraceCase[]
  facts: string[]
  sqls: TraceSql[]
  decisions: TraceDecision[]
}

// ── 訊息型別 ──────────────────────────────────────────────────────────────────

export interface UserMessage {
  id: string
  role: 'user'
  text: string
}

export interface AgentMessage {
  id: string
  role: 'agent'
  text: string
  thinking: ThinkingData
  status: 'streaming' | 'done' | 'error'
}

export interface SqlConfirmMessage {
  id: string
  role: 'sql_confirm'
  sql: string
  reply: string
  handled: boolean
}

export interface AskUserMessage {
  id: string
  role: 'ask_user'
  reply: string
}

export interface CollectParamsMessage {
  id: string
  role: 'collect_params'
  params: string[]
  handled: boolean
}

export interface SqlErrorMessage {
  id: string
  role: 'sql_error'
  error_message: string
  sql: string
  hint: string
}

export interface SelectCaseMessage {
  id: string
  role: 'select_case'
  candidates: Array<{ case_id: string; title: string; symptom: string }>
  reply: string
  handled: boolean
}

export interface ClarifyMessage {
  id: string
  role: 'clarify'
  reply: string
  options: string[]
  handled: boolean
}

export type ChatMessage =
  | UserMessage
  | AgentMessage
  | SqlConfirmMessage
  | AskUserMessage
  | CollectParamsMessage
  | SqlErrorMessage
  | SelectCaseMessage
  | ClarifyMessage

// ── Session ───────────────────────────────────────────────────────────────────

export interface ChatSession {
  sessionId: string
  name: string
  createdAt?: string
  messages: ChatMessage[]
}
