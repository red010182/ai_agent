import { useState } from 'react'
import { Button, Input } from 'antd'
import type { ClarifyMessage } from '../types'

interface Props {
  message: ClarifyMessage
  onSelect: (option: string) => void
}

export function ClarifyCard({ message, onSelect }: Props) {
  const [showInput, setShowInput] = useState(false)
  const [customText, setCustomText] = useState('')

  const handleOption = (option: string) => {
    if (message.handled) return
    onSelect(option)
  }

  const handleOther = () => {
    if (message.handled) return
    setShowInput(true)
  }

  const handleCustomSubmit = () => {
    const text = customText.trim()
    if (!text) return
    onSelect(text)
  }

  return (
    <div className="max-w-[85%] rounded-xl border border-violet-200 bg-violet-50 px-4 py-3 text-sm">
      <p className="font-medium text-violet-700 mb-2">🤔 需要更多資訊</p>
      <p className="text-violet-800 mb-3">{message.reply}</p>
      <div className="flex flex-wrap gap-2">
        {message.options.map(opt => (
          <Button
            key={opt}
            size="small"
            disabled={message.handled}
            onClick={() => handleOption(opt)}
            className="text-left"
          >
            {opt}
          </Button>
        ))}
        <Button
          size="small"
          disabled={message.handled}
          onClick={handleOther}
        >
          ✏️ 其他（自由輸入）
        </Button>
      </div>
      {showInput && !message.handled && (
        <div className="flex gap-2 mt-3">
          <Input
            size="small"
            value={customText}
            onChange={e => setCustomText(e.target.value)}
            onPressEnter={handleCustomSubmit}
            placeholder="請輸入您的回答..."
            autoFocus
            className="flex-1"
          />
          <Button
            size="small"
            type="primary"
            disabled={!customText.trim()}
            onClick={handleCustomSubmit}
          >
            送出
          </Button>
        </div>
      )}
    </div>
  )
}
