import { Button } from 'antd'
import { PlusOutlined, DeleteOutlined, MessageOutlined } from '@ant-design/icons'
import type { ChatSession } from '../types'

interface Props {
  sessions: ChatSession[]
  currentSessionId: string | null
  onNew: () => void
  onSelect: (id: string) => void
  onDelete: (id: string) => void
}

export function SessionSider({ sessions, currentSessionId, onNew, onSelect, onDelete }: Props) {
  return (
    <div className="flex flex-col h-full">
      {/* 新增按鈕 */}
      <div className="p-3 border-b border-gray-200">
        <Button type="dashed" icon={<PlusOutlined />} onClick={onNew} block>
          新增聊天室
        </Button>
      </div>

      {/* Session 列表 */}
      <div className="flex-1 overflow-y-auto">
        {sessions.length === 0 ? (
          <p className="p-4 text-center text-gray-400 text-sm">尚無聊天室</p>
        ) : (
          sessions.map(session => {
            const active = session.sessionId === currentSessionId
            const timeLabel = session.createdAt
              ? (() => {
                  const d = new Date(session.createdAt)
                  const mm = String(d.getMonth() + 1).padStart(2, '0')
                  const dd = String(d.getDate()).padStart(2, '0')
                  const hh = String(d.getHours()).padStart(2, '0')
                  const min = String(d.getMinutes()).padStart(2, '0')
                  return `${mm}/${dd} ${hh}:${min}`
                })()
              : null
            return (
              <div
                key={session.sessionId}
                onClick={() => onSelect(session.sessionId)}
                className={[
                  'flex items-center gap-2 px-3 py-2 cursor-pointer',
                  'hover:bg-gray-50 transition-colors group',
                  active ? 'bg-blue-50 border-r-2 border-blue-500' : '',
                ].join(' ')}
              >
                <MessageOutlined className="text-gray-400 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p
                    className={[
                      'text-sm truncate m-0 leading-snug',
                      active ? 'text-blue-600 font-medium' : 'text-gray-700',
                    ].join(' ')}
                  >
                    {session.name}
                  </p>
                  {timeLabel && (
                    <p className="text-xs text-gray-400 m-0 leading-snug">{timeLabel}</p>
                  )}
                </div>
                <Button
                  type="text"
                  size="small"
                  danger
                  icon={<DeleteOutlined />}
                  className="opacity-0 group-hover:opacity-100 shrink-0"
                  onClick={e => { e.stopPropagation(); onDelete(session.sessionId) }}
                />
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
