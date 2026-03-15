import { Collapse, Progress, Tag, Table } from 'antd'
import type { ThinkingData, TraceSql } from '../types'

interface Props {
  thinking: ThinkingData
  status: 'streaming' | 'done' | 'error'
}

export function ThinkingPanel({ thinking, status }: Props) {
  const hasContent =
    thinking.routing ||
    thinking.cases.length > 0 ||
    thinking.facts.length > 0 ||
    thinking.sqls.length > 0

  if (!hasContent) return null

  const innerItems = []

  // ① 路由決策
  if (thinking.routing) {
    const r = thinking.routing
    innerItems.push({
      key: 'routing',
      label: <span className="text-xs">🔍 路由決策</span>,
      children: (
        <div className="space-y-1.5 text-xs">
          {r.mode === 'sop' ? (
            <>
              <div>
                <span className="text-gray-400">SOP：</span>
                <span className="font-mono">{r.matched_sop}</span>
              </div>
              <div>
                <span className="text-gray-400">Case：</span>
                <span>{r.matched_case}</span>
                {r.case_title && <span className="text-gray-500"> — {r.case_title}</span>}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-gray-400 shrink-0">相似度：</span>
                <Progress
                  percent={Math.round(r.score * 100)}
                  size="small"
                  status={r.score >= 0.7 ? 'success' : 'exception'}
                  className="flex-1 max-w-48"
                />
              </div>
            </>
          ) : (
            <div className="text-orange-500">
              未找到對應 SOP（score: {r.score.toFixed(2)}）
            </div>
          )}
        </div>
      ),
    })
  }

  // ② 執行軌跡
  if (thinking.cases.length > 0) {
    innerItems.push({
      key: 'cases',
      label: <span className="text-xs">⚙️ 執行軌跡</span>,
      children: (
        <div className="flex flex-wrap items-center gap-1 text-xs">
          {thinking.cases.map((c, i) => {
            const isCurrent = i === thinking.cases.length - 1
            return (
              <span key={`${c.case_id}-${i}`} className="flex items-center gap-1">
                {i > 0 && <span className="text-gray-300">→</span>}
                <Tag
                  color={isCurrent && status === 'streaming' ? 'blue' : isCurrent ? 'green' : 'default'}
                  className="text-xs"
                >
                  {!isCurrent && '✓ '}{c.case_id}
                  {c.case_title && ` ${c.case_title}`}
                </Tag>
              </span>
            )
          })}
        </div>
      ),
    })
  }

  // ③ 已知狀態（known_facts）
  if (thinking.facts.length > 0) {
    innerItems.push({
      key: 'facts',
      label: <span className="text-xs">📋 已知狀態（{thinking.facts.length} 條）</span>,
      children: (
        <ul className="space-y-1 text-xs list-none p-0 m-0">
          {thinking.facts.map((f, i) => (
            <li
              key={f}
              className={`flex gap-1.5 ${i === thinking.facts.length - 1 ? 'fact-new' : ''}`}
            >
              <span className="text-gray-400 shrink-0">•</span>
              <span className="text-gray-700">{f}</span>
            </li>
          ))}
        </ul>
      ),
    })
  }

  // ④ SQL 記錄
  if (thinking.sqls.length > 0) {
    innerItems.push({
      key: 'sqls',
      label: <span className="text-xs">🗄️ SQL 記錄（{thinking.sqls.length} 次查詢）</span>,
      children: (
        <div className="space-y-2">
          {thinking.sqls.map((sql, i) => (
            <SqlRecord key={i} sql={sql} index={i + 1} />
          ))}
        </div>
      ),
    })
  }

  return (
    <Collapse
      size="small"
      ghost
      className="bg-gray-50 rounded-lg border border-gray-100 text-xs max-w-full"
      items={[{
        key: 'thinking',
        label: <span className="text-gray-400 text-xs">🤖 Agent 思考過程</span>,
        children: <Collapse size="small" ghost items={innerItems} />,
      }]}
    />
  )
}

// SQL 子記錄卡片
function SqlRecord({ sql, index }: { sql: TraceSql; index: number }) {
  const columns =
    sql.result_preview.length > 0
      ? Object.keys(sql.result_preview[0]).map(k => ({
          title: k,
          dataIndex: k,
          key: k,
          ellipsis: true,
          render: (v: unknown) => String(v ?? ''),
        }))
      : []

  const displayRows = sql.result_preview.slice(0, 10).map((r, i) => ({ ...r, _rowKey: i }))

  return (
    <Collapse
      size="small"
      ghost
      className="bg-white border border-gray-200 rounded-lg"
      items={[{
        key: 'sql',
        label: (
          <span className="text-xs font-mono text-gray-600">
            查詢 #{index} — 回傳 {sql.result_rows} 筆
          </span>
        ),
        children: (
          <div className="space-y-2 text-xs">
            <div>
              <p className="text-gray-400 mb-1">原始 template</p>
              <pre className="bg-gray-100 rounded p-2 font-mono overflow-x-auto whitespace-pre-wrap">
                {sql.template}
              </pre>
            </div>
            <div>
              <p className="text-gray-400 mb-1">填入參數後</p>
              <pre className="bg-blue-50 border border-blue-100 rounded p-2 font-mono overflow-x-auto whitespace-pre-wrap">
                {sql.filled}
              </pre>
            </div>
            {columns.length > 0 && (
              <div>
                <p className="text-gray-400 mb-1">
                  查詢結果（共 {sql.result_rows} 筆
                  {sql.result_rows > 10 && `，顯示前 10 筆`}）
                </p>
                <Table
                  dataSource={displayRows}
                  rowKey="_rowKey"
                  columns={columns}
                  pagination={false}
                  size="small"
                  scroll={{ x: true }}
                />
              </div>
            )}
          </div>
        ),
      }]}
    />
  )
}
