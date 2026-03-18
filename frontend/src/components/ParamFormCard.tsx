import { useState } from 'react'
import { Button, Form, Input } from 'antd'
import { FormOutlined } from '@ant-design/icons'
import type { CollectParamsMessage } from '../types'

interface Props {
  message: CollectParamsMessage
  onSubmit: (params: Record<string, string>) => void
}

export function ParamFormCard({ message, onSubmit }: Props) {
  const [form] = Form.useForm<Record<string, string>>()
  const [submitted, setSubmitted] = useState(message.handled)

  const handleFinish = (values: Record<string, string>) => {
    setSubmitted(true)
    onSubmit(values)
  }

  return (
    <div
      className={[
        'max-w-[80%] rounded-xl border border-green-200 bg-green-50 p-4 space-y-3',
        submitted ? 'opacity-50' : '',
      ].join(' ')}
    >
      <div className="flex items-center justify-between">
        <span className="font-medium text-green-700 text-sm">
          <FormOutlined className="mr-1" />
          需要填寫參數
        </span>
        {submitted && <span className="text-xs text-gray-400">已送出</span>}
      </div>

      <Form
        form={form}
        layout="vertical"
        onFinish={handleFinish}
        disabled={submitted}
        size="small"
      >
        {message.params.map(name => (
          <Form.Item
            key={name}
            name={name}
            label={<span className="text-sm font-mono text-gray-700">{name}</span>}
            rules={[{ required: true, message: `請填寫 ${name}` }]}
            className="mb-2"
          >
            <Input placeholder={name} />
          </Form.Item>
        ))}

        {!submitted && (
          <Form.Item className="mb-0 mt-3">
            <Button type="primary" htmlType="submit" size="small">
              送出參數
            </Button>
          </Form.Item>
        )}
      </Form>
    </div>
  )
}
