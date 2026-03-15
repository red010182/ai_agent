import { Button, Space } from 'antd'
import { CheckOutlined, CloseOutlined } from '@ant-design/icons'
import type { SqlConfirmMessage } from '../types'

interface Props {
  message: SqlConfirmMessage
  onConfirm: (answer: 'yes' | 'no') => void
}

export function SqlConfirmCard({ message, onConfirm }: Props) {
  return (
    <div
      className={[
        'max-w-[80%] rounded-xl border border-blue-200 bg-blue-50 p-4 space-y-3',
        message.handled ? 'opacity-50' : '',
      ].join(' ')}
    >
      <div className="flex items-center justify-between">
        <span className="font-medium text-blue-700 text-sm">🗄️ SQL 確認</span>
        {!message.handled && (
          <Space size="small">
            <Button
              type="primary"
              size="small"
              icon={<CheckOutlined />}
              onClick={() => onConfirm('yes')}
            >
              確認執行
            </Button>
            <Button
              danger
              size="small"
              icon={<CloseOutlined />}
              onClick={() => onConfirm('no')}
            >
              取消
            </Button>
          </Space>
        )}
        {message.handled && (
          <span className="text-xs text-gray-400">已處理</span>
        )}
      </div>
      {message.reply && (
        <p className="text-sm text-gray-600">{message.reply}</p>
      )}
      <pre className="bg-white border border-blue-100 rounded-lg p-3 text-xs font-mono overflow-x-auto">
        {message.sql}
      </pre>
    </div>
  )
}
