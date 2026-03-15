import { useState, useCallback } from 'react'
import { Layout } from 'antd'
import { SessionSider } from './components/SessionSider'
import { ChatArea } from './components/ChatArea'
import { createSession, deleteSession } from './api'
import type { ChatSession, ChatMessage } from './types'

const { Sider, Content } = Layout

export default function App() {
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null)

  const handleNew = useCallback(async () => {
    try {
      const { session_id } = await createSession()
      const newSession: ChatSession = {
        sessionId: session_id,
        name: `聊天室 ${new Date().toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' })}`,
        messages: [],
      }
      setSessions(prev => [newSession, ...prev])
      setCurrentSessionId(session_id)
    } catch (err) {
      console.error('建立 session 失敗', err)
    }
  }, [])

  const handleSelect = useCallback((id: string) => {
    setCurrentSessionId(id)
  }, [])

  const handleDelete = useCallback(async (id: string) => {
    try {
      await deleteSession(id)
    } catch {
      // ignore
    }
    setSessions(prev => prev.filter(s => s.sessionId !== id))
    setCurrentSessionId(prev => (prev === id ? null : prev))
  }, [])

  const addMessage = useCallback((sessionId: string, msg: ChatMessage) => {
    setSessions(prev =>
      prev.map(s =>
        s.sessionId === sessionId
          ? { ...s, messages: [...s.messages, msg] }
          : s,
      ),
    )
  }, [])

  const updateMessage = useCallback(
    (sessionId: string, msgId: string, updater: (m: ChatMessage) => ChatMessage) => {
      setSessions(prev =>
        prev.map(s =>
          s.sessionId === sessionId
            ? {
                ...s,
                messages: s.messages.map(m => (m.id === msgId ? updater(m) : m)),
              }
            : s,
        ),
      )
    },
    [],
  )

  const currentSession = sessions.find(s => s.sessionId === currentSessionId) ?? null

  return (
    <Layout className="h-screen">
      <Sider
        width={240}
        className="bg-white border-r border-gray-200"
        style={{ overflow: 'hidden' }}
      >
        <div className="h-full flex flex-col">
          <div className="px-4 py-3 border-b border-gray-200">
            <h1 className="text-base font-semibold text-gray-800 m-0">智能客服 Agent</h1>
            <p className="text-xs text-gray-400 mt-0.5 mb-0">製程疑難雜症查詢</p>
          </div>
          <div className="flex-1 overflow-hidden">
            <SessionSider
              sessions={sessions}
              currentSessionId={currentSessionId}
              onNew={handleNew}
              onSelect={handleSelect}
              onDelete={handleDelete}
            />
          </div>
        </div>
      </Sider>
      <Content className="flex flex-col overflow-hidden">
        {currentSession ? (
          <ChatArea
            session={currentSession}
            addMessage={addMessage}
            updateMessage={updateMessage}
          />
        ) : (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm flex-col gap-3">
            <span className="text-4xl">🤖</span>
            <p className="m-0">點擊左側「新增聊天室」開始對話</p>
          </div>
        )}
      </Content>
    </Layout>
  )
}
