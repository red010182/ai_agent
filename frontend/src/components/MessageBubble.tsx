import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import type { ChatMessage } from '../types'
import { ThinkingPanel } from './ThinkingPanel'
import { SqlConfirmCard } from './SqlConfirmCard'
import { SqlErrorCard } from './SqlErrorCard'
import { ParamFormCard } from './ParamFormCard'

interface Props {
  message: ChatMessage
  onSqlConfirm: (msgId: string, answer: 'yes' | 'no') => void
  onParamSubmit: (msgId: string, params: Record<string, string>) => void
}

export function MessageBubble({ message, onSqlConfirm, onParamSubmit }: Props) {
  // 用戶訊息：右對齊
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[70%] bg-blue-500 text-white rounded-2xl rounded-tr-sm px-4 py-2 text-sm whitespace-pre-wrap break-words">
          {message.text}
        </div>
      </div>
    )
  }

  // SQL 確認卡片
  if (message.role === 'sql_confirm') {
    return (
      <div className="flex justify-start">
        <SqlConfirmCard
          message={message}
          onConfirm={answer => onSqlConfirm(message.id, answer)}
        />
      </div>
    )
  }

  // SQL 執行錯誤卡片
  if (message.role === 'sql_error') {
    return (
      <div className="flex justify-start">
        <SqlErrorCard message={message} />
      </div>
    )
  }

  // 參數表單
  if (message.role === 'collect_params') {
    return (
      <div className="flex justify-start">
        <ParamFormCard
          message={message}
          onSubmit={params => onParamSubmit(message.id, params)}
        />
      </div>
    )
  }

  // 補充資訊提示
  if (message.role === 'ask_user') {
    return (
      <div className="flex justify-start">
        <div className="max-w-[80%] rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm">
          <p className="font-medium text-amber-700 mb-1">💬 需要補充資訊</p>
          <p className="text-amber-800">{message.reply}</p>
        </div>
      </div>
    )
  }

  // Agent 回覆（左對齊 + 思考過程）
  const showBubble = !!message.text || message.status === 'streaming' || message.status === 'error'
  return (
    <div className="flex flex-col gap-1.5 max-w-[80%]">
      {showBubble && (
        <div className="bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm text-sm text-gray-800">
          {message.text ? (
            <div className="prose prose-sm max-w-none">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  pre({ children }) {
                    // v10: block code is <pre><code>…</code></pre>
                    // Let code handler take full control; strip the outer <pre>
                    return <>{children}</>
                  },
                  code({ className, children }) {
                    const match = /language-(\w+)/.exec(className || '')
                    return match ? (
                      <SyntaxHighlighter
                        style={vscDarkPlus}
                        language={match[1]}
                        PreTag="div"
                      >
                        {String(children).replace(/\n$/, '')}
                      </SyntaxHighlighter>
                    ) : (
                      <code className={className}>{children}</code>
                    )
                  },
                }}
              >
                {message.text}
              </ReactMarkdown>
            </div>
          ) : (
            message.status === 'streaming' && (
              <span className="text-gray-400 text-xs">正在思考中...</span>
            )
          )}
          {/* 串流游標 */}
          {message.status === 'streaming' && message.text && (
            <span className="inline-block w-0.5 h-4 ml-0.5 bg-gray-500 animate-pulse align-middle" />
          )}
          {message.status === 'error' && (
            <span className="text-red-500 text-xs ml-1">⚠️ 發生錯誤</span>
          )}
        </div>
      )}
      <ThinkingPanel thinking={message.thinking} status={message.status} />
    </div>
  )
}
