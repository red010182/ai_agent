import { Button } from 'antd'
import type { SelectCaseMessage } from '../types'

interface Props {
  message: SelectCaseMessage
  onSelect: (caseId: string) => void
}

export function SelectCaseCard({ message, onSelect }: Props) {
  return (
    <div className="max-w-[85%] rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm">
      <p className="font-medium text-blue-700 mb-2">🔍 找到多個可能相關的情況</p>
      <p className="text-blue-800 mb-3">{message.reply}</p>
      <div className="flex flex-col gap-2">
        {message.candidates.map(c => (
          <div
            key={c.case_id}
            className="flex items-start justify-between gap-3 rounded-lg border border-blue-200 bg-white px-3 py-2"
          >
            <div className="flex-1 min-w-0">
              <p className="font-medium text-gray-800 text-sm">{c.title}</p>
              <p className="text-gray-500 text-xs mt-0.5 break-words">{c.symptom}</p>
            </div>
            <Button
              type="primary"
              size="small"
              disabled={message.handled}
              onClick={() => onSelect(c.case_id)}
              className="shrink-0"
            >
              選擇
            </Button>
          </div>
        ))}
      </div>
    </div>
  )
}
