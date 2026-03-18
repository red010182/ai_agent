import type { SqlErrorMessage } from '../types'

interface Props {
  message: SqlErrorMessage
}

export function SqlErrorCard({ message }: Props) {
  return (
    <div className="max-w-[85%] rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm space-y-2">
      <p className="font-semibold text-red-700">⚠️ SQL 執行失敗</p>

      <div>
        <p className="text-xs text-red-500 font-medium mb-1">DB 錯誤訊息</p>
        <pre className="bg-red-100 text-red-800 rounded px-3 py-2 text-xs font-mono whitespace-pre-wrap break-all">
          {message.error_message}
        </pre>
      </div>

      <div>
        <p className="text-xs text-gray-500 font-medium mb-1">執行的 SQL</p>
        <pre className="bg-gray-100 text-gray-800 rounded px-3 py-2 text-xs font-mono whitespace-pre-wrap break-all">
          {message.sql}
        </pre>
      </div>

      <p className="text-gray-600 text-xs border-t border-red-200 pt-2">{message.hint}</p>
    </div>
  )
}
