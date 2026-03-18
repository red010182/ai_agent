import { useState, useCallback, useRef, useEffect } from 'react'
import { Input, Button } from 'antd'
import { SendOutlined } from '@ant-design/icons'
import { streamChat } from '../api'
import type { ChatMessage, ChatSession, ThinkingData } from '../types'
import { MessageBubble } from './MessageBubble'

interface Props {
  session: ChatSession
  addMessage: (sessionId: string, msg: ChatMessage) => void
  updateMessage: (
    sessionId: string,
    msgId: string,
    updater: (m: ChatMessage) => ChatMessage,
  ) => void
}

function emptyThinking(): ThinkingData {
  return { cases: [], facts: [], sqls: [], decisions: [] }
}

export function ChatArea({ session, addMessage, updateMessage }: Props) {
  const [inputText, setInputText] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  // 新訊息時自動滾到底
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [session.messages.length])

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim()
      if (!trimmed || isStreaming) return
      setInputText('')
      setIsStreaming(true)

      // 加入用戶訊息
      addMessage(session.sessionId, {
        id: crypto.randomUUID(),
        role: 'user',
        text: trimmed,
      })

      // 加入空的 agent 訊息（streaming 狀態）
      const agentMsgId = crypto.randomUUID()
      addMessage(session.sessionId, {
        id: agentMsgId,
        role: 'agent',
        text: '',
        thinking: emptyThinking(),
        status: 'streaming',
      })

      const upd = (
        updater: (m: ChatMessage) => ChatMessage,
      ) => updateMessage(session.sessionId, agentMsgId, updater)

      try {
        for await (const evt of streamChat(session.sessionId, trimmed)) {
          switch (evt.type) {
            case 'text_delta':
              upd(m => m.role === 'agent' ? { ...m, text: m.text + evt.content } : m)
              break
            case 'sql_confirm':
              addMessage(session.sessionId, {
                id: crypto.randomUUID(),
                role: 'sql_confirm',
                sql: evt.sql,
                reply: evt.reply,
                handled: false,
              })
              break
            case 'ask_user':
              addMessage(session.sessionId, {
                id: crypto.randomUUID(),
                role: 'ask_user',
                reply: evt.reply,
              })
              break
            case 'collect_params':
              addMessage(session.sessionId, {
                id: crypto.randomUUID(),
                role: 'collect_params',
                params: evt.params,
                handled: false,
              })
              break
            case 'trace_routing':
              upd(m => m.role === 'agent'
                ? { ...m, thinking: { ...m.thinking, routing: evt } }
                : m)
              break
            case 'trace_case':
              upd(m => m.role === 'agent'
                ? { ...m, thinking: { ...m.thinking, cases: [...m.thinking.cases, evt] } }
                : m)
              break
            case 'trace_facts':
              upd(m => m.role === 'agent'
                ? { ...m, thinking: { ...m.thinking, facts: evt.known_facts } }
                : m)
              break
            case 'trace_sql':
              upd(m => m.role === 'agent'
                ? { ...m, thinking: { ...m.thinking, sqls: [...m.thinking.sqls, evt] } }
                : m)
              break
            case 'trace_decision':
              upd(m => m.role === 'agent'
                ? { ...m, thinking: { ...m.thinking, decisions: [...m.thinking.decisions, evt] } }
                : m)
              break
            case 'sql_error':
              addMessage(session.sessionId, {
                id: crypto.randomUUID(),
                role: 'sql_error',
                error_message: evt.error_message,
                sql: evt.sql,
                hint: evt.hint,
              })
              break
            case 'done':
              upd(m => m.role === 'agent' ? { ...m, status: 'done' } : m)
              break
            case 'error':
              upd(m => m.role === 'agent'
                ? { ...m, status: 'error', text: m.text + `\n\n⚠️ ${evt.message}` }
                : m)
              break
          }
        }
      } catch (err) {
        upd(m => m.role === 'agent'
          ? { ...m, status: 'error', text: m.text + '\n\n⚠️ 連線錯誤' }
          : m)
      } finally {
        setIsStreaming(false)
      }
    },
    [session.sessionId, isStreaming, addMessage, updateMessage],
  )

  // SQL 確認按鈕：標記已處理，發送 yes/no
  const handleSqlConfirm = useCallback(
    (msgId: string, answer: 'yes' | 'no') => {
      updateMessage(session.sessionId, msgId, m =>
        m.role === 'sql_confirm' ? { ...m, handled: true } : m,
      )
      sendMessage(answer)
    },
    [session.sessionId, updateMessage, sendMessage],
  )

  // 參數表單送出：標記已處理，以 JSON 字串送出參數
  const handleParamSubmit = useCallback(
    (msgId: string, params: Record<string, string>) => {
      updateMessage(session.sessionId, msgId, m =>
        m.role === 'collect_params' ? { ...m, handled: true } : m,
      )
      sendMessage(JSON.stringify(params))
    },
    [session.sessionId, updateMessage, sendMessage],
  )

  return (
    <div className="flex flex-col h-full bg-gray-50">
      {/* 訊息列表 */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {session.messages.length === 0 && (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            請描述您遇到的製程問題
          </div>
        )}
        {session.messages.map(msg => (
          <MessageBubble
            key={msg.id}
            message={msg}
            onSqlConfirm={handleSqlConfirm}
            onParamSubmit={handleParamSubmit}
          />
        ))}
        <div ref={bottomRef} />
      </div>

      {/* 輸入列 */}
      <div className="border-t border-gray-200 bg-white px-4 py-3 flex gap-2">
        <Input
          value={inputText}
          onChange={e => setInputText(e.target.value)}
          onPressEnter={() => sendMessage(inputText)}
          placeholder={isStreaming ? 'Agent 處理中...' : '輸入問題或指令（Enter 送出）'}
          disabled={isStreaming}
          className="flex-1"
          size="large"
        />
        <Button
          type="primary"
          size="large"
          icon={<SendOutlined />}
          disabled={isStreaming || !inputText.trim()}
          onClick={() => sendMessage(inputText)}
        >
          送出
        </Button>
      </div>
    </div>
  )
}
