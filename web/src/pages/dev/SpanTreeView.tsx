// D-01 Trace 决策树主页面（shadcn/ui 重写）。
// 设计来源：13 号文档第 4 节。
// 范围：工单 ID 输入 + span 树形展示 + 点击 span 弹 Sheet 看详情含决策五元组。

import { useEffect, useMemo, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeChange,
  type NodeProps,
  type NodeTypes,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { cn } from '@/lib/utils'
import {
  Bell,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  CircleDot,
  FileWarning,
  GitBranch,
  HelpCircle,
  Inbox,
  Loader2,
  Maximize2,
  MessageSquareText,
  RotateCcw,
  Route as RouteIcon,
  Search as SearchIcon,
  ShieldCheck,
  Tags,
  TriangleAlert,
  UserRoundCheck,
  Zap,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import type { AdminSpanDecision, AdminTraceDetail, Span } from '@/types'

// 决策类型徽章颜色（详见 13 号文档第 4.3 节）
const DECISION_TYPE_COLOR: Record<string, string> = {
  routing: 'bg-blue-500/15 text-blue-300 border-blue-500/30',
  branching: 'bg-violet-500/15 text-violet-300 border-violet-500/30',
  tool_selection: 'bg-slate-500/15 text-slate-300 border-slate-500/30',
  quality_gate: 'bg-cyan-500/15 text-cyan-300 border-cyan-500/30',
  boundary: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  escalation: 'bg-rose-500/15 text-rose-300 border-rose-500/30',
}

// 置信度色阶
function confidenceClass(conf: number | null | undefined): string {
  if (conf == null) return 'text-muted-foreground'
  if (conf < 0.5) return 'text-rose-400'
  if (conf < 0.7) return 'text-amber-400'
  if (conf < 0.9) return 'text-yellow-300'
  return 'text-emerald-400'
}

function statusBadgeClass(status: string | null | undefined): string {
  if (status === 'ok') return 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
  if (status === 'error') return 'bg-rose-500/15 text-rose-300 border-rose-500/30'
  if (status === 'fallback') return 'bg-amber-500/15 text-amber-300 border-amber-500/30'
  return 'bg-muted text-muted-foreground'
}

function formatDuration(duration: number | null | undefined): string {
  if (duration == null) return '-'
  if (duration < 1) return `${Math.round(duration * 1000)}ms`
  return `${duration.toFixed(2)}s`
}

const WORKFLOW_NODE_WIDTH = 176
const WORKFLOW_NODE_HEIGHT = 104

type WorkflowNodeConfig = {
  id: string
  label: string
  role: string
  note: string
  x: number
  y: number
  icon: LucideIcon
  targetPosition?: Position
  sourcePosition?: Position
}

type WorkflowEdgeConfig = {
  from: string
  to: string
  label?: string
  dashed?: boolean
}

type WorkflowNodeData = {
  config: WorkflowNodeConfig
  span?: Span
  hasDecision: boolean
  onSelect: (spanId: string) => void
}

const WORKFLOW_NODE_TYPES: NodeTypes = {
  workflow: WorkflowNode,
}

const WORKFLOW_NODES: WorkflowNodeConfig[] = [
  {
    id: 'receive',
    label: '接收工单',
    role: '入口节点',
    note: '创建状态与 trace',
    x: 0,
    y: 260,
    icon: Inbox,
  },
  {
    id: 'classify',
    label: '意图分类',
    role: 'Classifier Agent',
    note: '分类、优先级、风险',
    x: 260,
    y: 260,
    icon: Tags,
  },
  {
    id: 'route',
    label: '条件路由',
    role: 'Router',
    note: '自动答复/处理/升级',
    x: 520,
    y: 260,
    icon: RouteIcon,
  },
  {
    id: 'auto_reply',
    label: '自动答复',
    role: '低风险分支',
    note: '咨询类快速响应',
    x: 800,
    y: 80,
    icon: MessageSquareText,
  },
  {
    id: 'process',
    label: '智能处理',
    role: 'Processor Agent',
    note: 'RAG + 工具调用',
    x: 800,
    y: 260,
    icon: Zap,
  },
  {
    id: 'escalate',
    label: '升级处理',
    role: '风险分支',
    note: '触发人工审核',
    x: 800,
    y: 440,
    icon: TriangleAlert,
  },
  {
    id: 'review',
    label: '质量审核',
    role: 'Reviewer Agent',
    note: '评分与返工判断',
    x: 1080,
    y: 260,
    icon: ShieldCheck,
  },
  {
    id: 'finalize_knowledge_gap',
    label: '知识缺口归档',
    role: '兜底节点',
    note: '不可重试场景',
    x: 1360,
    y: 0,
    icon: FileWarning,
  },
  {
    id: 'retry_check',
    label: '重试检查',
    role: 'Retry Gate',
    note: '返工次数控制',
    x: 1360,
    y: 260,
    icon: RotateCcw,
  },
  {
    id: 'request_user_input',
    label: '请求补充',
    role: '用户交互节点',
    note: '等待用户补充信息',
    x: 1360,
    y: 520,
    icon: HelpCircle,
  },
  {
    id: 'notify',
    label: '结果通知',
    role: 'Notification',
    note: '写回处理结果',
    x: 1640,
    y: 80,
    icon: Bell,
  },
  {
    id: 'human_review_wait',
    label: '人工审核等待',
    role: 'Human Review',
    note: '生成审核单',
    x: 1640,
    y: 440,
    icon: UserRoundCheck,
  },
  {
    id: 'complete',
    label: '完成归档',
    role: '终态节点',
    note: '结束 LangGraph',
    x: 1920,
    y: 180,
    icon: CheckCircle2,
  },
  {
    id: 'handle_failure',
    label: '异常兜底',
    role: '失败终态',
    note: '错误处理与记录',
    x: 1920,
    y: 520,
    icon: TriangleAlert,
  },
]

const WORKFLOW_EDGES: WorkflowEdgeConfig[] = [
  { from: 'receive', to: 'classify' },
  { from: 'classify', to: 'route' },
  { from: 'route', to: 'auto_reply', label: '咨询' },
  { from: 'route', to: 'process', label: '处理' },
  { from: 'route', to: 'escalate', label: '升级' },
  { from: 'auto_reply', to: 'notify' },
  { from: 'process', to: 'review' },
  { from: 'review', to: 'finalize_knowledge_gap', label: '知识缺口' },
  { from: 'review', to: 'retry_check', label: '返工' },
  { from: 'review', to: 'request_user_input', label: '需补充' },
  { from: 'review', to: 'notify', label: '通过' },
  { from: 'finalize_knowledge_gap', to: 'notify' },
  { from: 'retry_check', to: 'process', label: '重试', dashed: true },
  { from: 'retry_check', to: 'human_review_wait', label: '超限' },
  { from: 'retry_check', to: 'notify', label: '兜底' },
  { from: 'escalate', to: 'human_review_wait' },
  { from: 'notify', to: 'complete' },
  { from: 'human_review_wait', to: 'complete', dashed: true },
]

export default function SpanTreeView() {
  const [ticketInput, setTicketInput] = useState('')
  const [activeTicketId, setActiveTicketId] = useState<string | null>(null)
  const [trace, setTrace] = useState<AdminTraceDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedSpan, setSelectedSpan] = useState<Span | null>(null)
  const [spanDetailLoading, setSpanDetailLoading] = useState(false)

  useEffect(() => {
    if (!activeTicketId) return
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      setLoading(true)
      setError(null)
      api
        .getAdminTrace(activeTicketId)
        .then((data) => {
          if (!cancelled) setTrace(data)
        })
        .catch((err: unknown) => {
          if (cancelled) return
          if (err instanceof ApiError && err.status === 404) {
            setError(`未找到工单 ${activeTicketId} 的 trace`)
          } else {
            setError(err instanceof Error ? err.message : '加载失败')
          }
          setTrace(null)
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })
    })
    return () => {
      cancelled = true
    }
  }, [activeTicketId])

  function handleSearch() {
    const trimmed = ticketInput.trim()
    if (!trimmed) return
    setActiveTicketId(trimmed)
    setSelectedSpan(null)
  }

  function handleSpanClick(spanId: string) {
    if (!activeTicketId) return
    setSpanDetailLoading(true)
    setSelectedSpan(null)
    api
      .getAdminSpanDetail(activeTicketId, spanId)
      .then((data) => setSelectedSpan(data))
      .catch(() => setSelectedSpan(null))
      .finally(() => setSpanDetailLoading(false))
  }

  return (
    <div className="space-y-4 p-4 md:p-6">
      <header>
        <h1 className="text-xl font-semibold">多 Agent 状态机画布</h1>
        <p className="text-sm text-muted-foreground">
          输入工单 ID 查看 LangGraph 节点编排、执行轨迹与决策点
        </p>
      </header>

      <Card>
        <CardContent className="flex flex-wrap gap-2">
          <Input
            className="max-w-md"
            placeholder="TK-20260704-XXXX"
            value={ticketInput}
            onChange={(e) => setTicketInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleSearch()
            }}
          />
          <Button onClick={handleSearch} disabled={loading || !ticketInput.trim()}>
            {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <SearchIcon className="mr-2 h-4 w-4" />}
            查询
          </Button>
          {trace && (
            <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <Badge variant="outline">trace: {trace.trace_id}</Badge>
              <Badge variant="outline">status: {trace.status}</Badge>
              <Badge variant="outline">tokens: {trace.total_tokens}</Badge>
              <Badge variant="outline">决策点: {trace.decision_count}</Badge>
            </div>
          )}
        </CardContent>
      </Card>

      <StateMachineCanvas trace={trace} onSelect={handleSpanClick} />

      {error && (
        <Card className="border-rose-500/40">
          <CardContent className="text-sm text-rose-300">{error}</CardContent>
        </Card>
      )}

      {loading && <Skeleton className="h-64 w-full" />}

      {!loading && trace && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
          {/* 左 + 中：span 树 */}
          <Card className="xl:col-span-2">
            <CardHeader>
              <CardTitle>Trace 证据树（点击查看详情）</CardTitle>
            </CardHeader>
            <CardContent>
              <SpanTreeRenderer
                spans={trace.spans}
                decisions={trace.decisions}
                onSelect={handleSpanClick}
              />
            </CardContent>
          </Card>

          {/* 右：决策时间线 */}
          <Card>
            <CardHeader>
              <CardTitle>决策时间线</CardTitle>
            </CardHeader>
            <CardContent>
              <DecisionTimeline
                decisions={trace.decisions}
                emptyState={trace.decision_empty_state?.message}
                onSelect={handleSpanClick}
              />
            </CardContent>
          </Card>
        </div>
      )}

      {/* Span 详情 Sheet */}
      <Sheet
        open={!!selectedSpan || spanDetailLoading}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedSpan(null)
            setSpanDetailLoading(false)
          }
        }}
      >
        <SheetContent className="w-full sm:max-w-2xl overflow-y-auto">
          {spanDetailLoading ? (
            <div className="flex h-full items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : selectedSpan ? (
            <SpanDetailPanel span={selectedSpan} />
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  )
}

function StateMachineCanvas({
  trace,
  onSelect,
}: {
  trace: AdminTraceDetail | null
  onSelect: (spanId: string) => void
}) {
  const flatSpans = useMemo(() => flattenSpans(trace?.spans ?? []), [trace?.spans])
  const spanByNode = useMemo(() => {
    const map = new Map<string, Span>()
    flatSpans.forEach((span) => {
      const normalized = normalizeWorkflowName(span.name)
      const matched = WORKFLOW_NODES.find((node) => normalized === node.id)
      if (matched && !map.has(matched.id)) {
        map.set(matched.id, span)
      }
    })
    return map
  }, [flatSpans])

  const decisionNodeIds = useMemo(() => {
    const ids = new Set<string>()
    trace?.decisions.forEach((decision) => {
      const normalized = normalizeWorkflowName(decision.span_name ?? '')
      WORKFLOW_NODES.forEach((node) => {
        if (normalized === node.id) ids.add(node.id)
      })
    })
    return ids
  }, [trace?.decisions])

  const executedCount = WORKFLOW_NODES.filter((node) => spanByNode.has(node.id)).length
  const errorCount = flatSpans.filter((span) => span.status === 'error').length
  const fallbackCount = flatSpans.filter((span) => span.status === 'fallback').length
  const baseNodes = useMemo<Node<WorkflowNodeData>[]>(
    () =>
      WORKFLOW_NODES.map((node) => ({
        id: node.id,
        type: 'workflow',
        position: { x: node.x, y: node.y },
        data: {
          config: node,
          span: spanByNode.get(node.id),
          hasDecision: decisionNodeIds.has(node.id),
          onSelect,
        },
        draggable: true,
        selectable: true,
        sourcePosition: node.sourcePosition ?? Position.Right,
        targetPosition: node.targetPosition ?? Position.Left,
      })),
    [decisionNodeIds, onSelect, spanByNode]
  )
  const baseEdges = useMemo<Edge[]>(
    () =>
      WORKFLOW_EDGES.map((edge) => {
        const active = spanByNode.has(edge.from) && spanByNode.has(edge.to)
        return {
          id: `${edge.from}-${edge.to}`,
          source: edge.from,
          target: edge.to,
          label: edge.label,
          animated: active && edge.dashed,
          type: 'smoothstep',
          markerEnd: {
            type: MarkerType.ArrowClosed,
            width: 18,
            height: 18,
          },
          style: {
            strokeWidth: active ? 2.2 : 1.4,
            strokeDasharray: edge.dashed ? '6 5' : undefined,
            stroke: active ? 'var(--primary)' : 'color-mix(in srgb, var(--muted-foreground) 42%, transparent)',
          },
          labelStyle: {
            fill: 'var(--muted-foreground)',
            fontSize: 11,
            fontWeight: 500,
          },
          labelBgStyle: {
            fill: 'var(--background)',
            fillOpacity: 0.92,
          },
          labelBgPadding: [6, 4] as [number, number],
          labelBgBorderRadius: 4,
        } satisfies Edge
      }),
    [spanByNode]
  )
  const [nodes, setNodes, onNodesChangeBase] = useNodesState(baseNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(baseEdges)
  const [fullscreenOpen, setFullscreenOpen] = useState(false)

  useEffect(() => {
    setNodes((current) =>
      baseNodes.map((node) => {
        const currentNode = current.find((item) => item.id === node.id)
        return currentNode ? { ...node, position: currentNode.position } : node
      })
    )
  }, [baseNodes, setNodes])

  useEffect(() => {
    setEdges(baseEdges)
  }, [baseEdges, setEdges])

  function handleNodesChange(changes: NodeChange<Node<WorkflowNodeData>>[]) {
    onNodesChangeBase(changes)
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <GitBranch className="h-4 w-4 text-primary" />
              LangGraph 状态机画布
            </CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              展示 receive → classify → route → process/review → notify/complete 的多智能体协同路径
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">节点 {WORKFLOW_NODES.length}</Badge>
            <Badge variant="outline">已执行 {executedCount}</Badge>
            <Badge variant="outline">决策 {trace?.decision_count ?? 0}</Badge>
            <Button
              variant="outline"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => setFullscreenOpen(true)}
            >
              <Maximize2 className="mr-1 h-3.5 w-3.5" />
              全屏
            </Button>
            {errorCount > 0 && (
              <Badge variant="outline" className="border-destructive/30 bg-destructive/10 text-destructive">
                异常 {errorCount}
              </Badge>
            )}
            {fallbackCount > 0 && (
              <Badge variant="outline" className="border-warning/30 bg-warning/10 text-warning">
                兜底 {fallbackCount}
              </Badge>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <WorkflowCanvasSurface
          nodes={nodes}
          edges={edges}
          heightClass="h-[600px]"
          onNodesChange={handleNodesChange}
          onEdgesChange={onEdgesChange}
        />
      </CardContent>
      <Dialog open={fullscreenOpen} onOpenChange={setFullscreenOpen}>
        <DialogContent className="flex h-[calc(100vh-2rem)] !w-[calc(100vw-2rem)] !max-w-[calc(100vw-2rem)] flex-col gap-3 border-border bg-card p-4">
          <DialogHeader className="shrink-0">
            <DialogTitle className="flex items-center gap-2 text-base">
              <GitBranch className="h-4 w-4 text-primary" />
              LangGraph 状态机画布
            </DialogTitle>
          </DialogHeader>
          <WorkflowCanvasSurface
            nodes={nodes}
            edges={edges}
            heightClass="h-full"
            className="flex-1"
            onNodesChange={handleNodesChange}
            onEdgesChange={onEdgesChange}
          />
        </DialogContent>
      </Dialog>
    </Card>
  )
}

function WorkflowCanvasSurface({
  nodes,
  edges,
  heightClass,
  className,
  onNodesChange,
  onEdgesChange,
}: {
  nodes: Node<WorkflowNodeData>[]
  edges: Edge[]
  heightClass: string
  className?: string
  onNodesChange: (changes: NodeChange<Node<WorkflowNodeData>>[]) => void
  onEdgesChange: (changes: Parameters<ReturnType<typeof useEdgesState<Edge>>[2]>[0]) => void
}) {
  return (
    <div
      className={cn(
        'relative min-h-0 overflow-hidden rounded-lg border border-border bg-background',
        heightClass,
        className
      )}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={WORKFLOW_NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        minZoom={0.28}
        maxZoom={1.6}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable
        panOnDrag={[1, 2]}
        selectionOnDrag
        proOptions={{ hideAttribution: true }}
        className="workflow-canvas"
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={24}
          size={1.1}
          color="color-mix(in srgb, var(--muted-foreground) 34%, transparent)"
        />
        <MiniMap
          pannable
          zoomable
          nodeStrokeWidth={2}
          nodeBorderRadius={8}
          nodeColor={(node) => miniMapNodeColor((node.data as WorkflowNodeData).span?.status)}
          maskColor="color-mix(in srgb, var(--background) 70%, transparent)"
          className="!border !border-border !bg-card/95"
        />
        <Controls
          showInteractive={false}
          className="!border !border-border !bg-card/95 [&_button]:!border-border [&_button]:!bg-card [&_button]:!text-foreground"
        />
      </ReactFlow>
      <div className="pointer-events-none absolute left-4 top-4 flex items-center gap-4 rounded-md border border-border bg-card/95 px-3 py-2 text-[11px] text-muted-foreground shadow-sm">
        <span className="flex items-center gap-1.5">
          <CircleDot className="h-3 w-3 text-primary" />
          Trace 高亮
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-success" />
          成功
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-warning" />
          兜底
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-destructive" />
          异常
        </span>
      </div>
    </div>
  )
}

function WorkflowNode({ data }: NodeProps<Node<WorkflowNodeData>>) {
  const { config, span, hasDecision, onSelect } = data
  const Icon = config.icon
  const content = (
    <>
      <Handle
        type="target"
        position={Position.Left}
        className="!h-2.5 !w-2.5 !border-border !bg-muted-foreground"
      />
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className={workflowIconClass(span?.status)}>
            <Icon className="h-4 w-4" />
          </span>
          <span className="truncate text-sm font-medium">{config.label}</span>
        </div>
        <span className={workflowStatusDotClass(span?.status)} />
      </div>
      <div className="mt-1 truncate font-mono text-[10px] text-muted-foreground">
        {config.id}
      </div>
      <div className="mt-2 flex items-center justify-between gap-2 text-[11px]">
        <span className="truncate text-muted-foreground">{config.role}</span>
        {span ? (
          <span className="font-mono text-foreground/80">{formatDuration(span.duration)}</span>
        ) : (
          <span className="text-muted-foreground/70">未触发</span>
        )}
      </div>
      <div className="mt-1 flex items-center justify-between gap-2">
        <span className="truncate text-[10px] text-muted-foreground/80">{config.note}</span>
        {hasDecision && (
          <Badge variant="outline" className="border-primary/30 bg-primary/10 px-1 py-0 text-[9px] text-primary">
            决策
          </Badge>
        )}
      </div>
      <Handle
        type="source"
        position={Position.Right}
        className="!h-2.5 !w-2.5 !border-border !bg-muted-foreground"
      />
    </>
  )

  if (span) {
    return (
      <button
        type="button"
        className={workflowNodeClass(span.status)}
        style={{ width: WORKFLOW_NODE_WIDTH, height: WORKFLOW_NODE_HEIGHT }}
        onClick={() => onSelect(span.span_id)}
      >
        {content}
      </button>
    )
  }

  return (
    <div
      className={workflowNodeClass(undefined)}
      style={{ width: WORKFLOW_NODE_WIDTH, height: WORKFLOW_NODE_HEIGHT }}
    >
      {content}
    </div>
  )
}

function flattenSpans(spans: Span[]): Span[] {
  return spans.flatMap((span) => [span, ...flattenSpans(span.children ?? [])])
}

function normalizeWorkflowName(value: string): string {
  return value.trim().toLowerCase().replace(/[\s-]+/g, '_')
}

function workflowNodeClass(status: string | undefined): string {
  const base =
    'relative rounded-lg border bg-card/95 p-3 text-left shadow-sm transition-colors'
  if (status === 'error') {
    return `${base} border-destructive/45 shadow-destructive/10 hover:border-destructive`
  }
  if (status === 'fallback') {
    return `${base} border-warning/45 shadow-warning/10 hover:border-warning`
  }
  if (status) {
    return `${base} border-primary/45 shadow-primary/10 hover:border-primary`
  }
  return `${base} border-border opacity-75`
}

function workflowIconClass(status: string | undefined): string {
  if (status === 'error') return 'rounded bg-destructive/15 p-1 text-destructive'
  if (status === 'fallback') return 'rounded bg-warning/15 p-1 text-warning'
  if (status) return 'rounded bg-primary/15 p-1 text-primary'
  return 'rounded bg-muted p-1 text-muted-foreground'
}

function workflowStatusDotClass(status: string | undefined): string {
  if (status === 'error') return 'mt-1 h-2.5 w-2.5 rounded-full bg-destructive'
  if (status === 'fallback') return 'mt-1 h-2.5 w-2.5 rounded-full bg-warning'
  if (status) return 'mt-1 h-2.5 w-2.5 rounded-full bg-success'
  return 'mt-1 h-2.5 w-2.5 rounded-full bg-muted-foreground/35'
}

function miniMapNodeColor(status: string | undefined): string {
  if (status === 'error') return 'var(--destructive)'
  if (status === 'fallback') return 'var(--warning)'
  if (status) return 'var(--success)'
  return 'var(--secondary)'
}

function SpanTreeRenderer({
  spans,
  decisions,
  onSelect,
}: {
  spans: Span[]
  decisions: AdminSpanDecision[]
  onSelect: (spanId: string) => void
}) {
  const decisionBySpan = useMemo(() => {
    const m = new Map<string, AdminSpanDecision>()
    decisions.forEach((d) => m.set(d.span_id, d))
    return m
  }, [decisions])
  if (spans.length === 0) {
    return <EmptyBlock description="该 trace 没有 span 数据" />
  }
  return (
    <div className="space-y-0.5">
      {spans.map((root) => (
        <SpanNode
          key={root.span_id}
          span={root}
          decision={decisionBySpan.get(root.span_id)}
          depth={0}
          onSelect={onSelect}
        />
      ))}
    </div>
  )
}

function SpanNode({
  span,
  decision,
  depth,
  onSelect,
}: {
  span: Span
  decision: AdminSpanDecision | undefined
  depth: number
  onSelect: (spanId: string) => void
}) {
  const [expanded, setExpanded] = useState(true)
  const hasChildren = (span.children?.length ?? 0) > 0
  return (
    <div>
      <div
        className="flex items-center gap-1 rounded px-1.5 py-1 hover:bg-muted/60 cursor-pointer"
        style={{ marginLeft: depth * 12 }}
        onClick={() => onSelect(span.span_id)}
      >
        {hasChildren ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              setExpanded((v) => !v)
            }}
            className="text-muted-foreground hover:text-foreground"
          >
            {expanded ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
          </button>
        ) : (
          <span className="inline-block w-3.5" />
        )}
        <span className="text-sm font-mono">{span.name}</span>
        <Badge variant="outline" className="ml-1 text-[10px]">
          {span.span_type}
        </Badge>
        <Badge variant="outline" className={`text-[10px] ${statusBadgeClass(span.status)}`}>
          {span.status}
        </Badge>
        <span className="ml-auto text-xs text-muted-foreground tabular-nums">
          {formatDuration(span.duration)}
        </span>
        {decision && (
          <Badge
            variant="outline"
            className={`text-[10px] ${DECISION_TYPE_COLOR[decision.decision_type ?? ''] ?? ''}`}
          >
            {decision.decision_type}
          </Badge>
        )}
      </div>
      {expanded &&
        hasChildren &&
        span.children.map((child) => (
          <SpanNode
            key={child.span_id}
            span={child}
            decision={undefined}
            depth={depth + 1}
            onSelect={onSelect}
          />
        ))}
    </div>
  )
}

function DecisionTimeline({
  decisions,
  emptyState,
  onSelect,
}: {
  decisions: AdminSpanDecision[]
  emptyState?: string
  onSelect: (spanId: string) => void
}) {
  if (decisions.length === 0) {
    return <EmptyBlock description={emptyState || '该 trace 无决策点埋点'} />
  }
  return (
    <ol className="relative space-y-3 border-l border-border pl-4">
      {decisions.map((d, idx) => (
        <li key={`${d.span_id}-${idx}`} className="relative">
          <span
            className={`absolute -left-[21px] top-1.5 h-3 w-3 rounded-full border-2 border-background ${
              DECISION_TYPE_COLOR[d.decision_type ?? '']
                ?.split(' ')
                .find((c) => c.startsWith('bg-')) ?? 'bg-muted'
            }`}
          />
          <button
            type="button"
            onClick={() => onSelect(d.span_id)}
            className="w-full text-left"
          >
            <div className="flex items-center gap-2">
              <span className="text-sm font-mono">{d.span_name}</span>
              <Badge variant="outline" className={`text-[10px] ${DECISION_TYPE_COLOR[d.decision_type ?? ''] ?? ''}`}>
                {d.decision_type}
              </Badge>
            </div>
            <div className="text-xs text-muted-foreground">
              选: <span className="font-medium text-foreground">{d.selection_value ?? '-'}</span>{' '}
              <span className={confidenceClass(d.confidence)}>
                置信度 {d.confidence != null ? d.confidence.toFixed(2) : '-'}
              </span>{' '}
              · {d.options_count} 候选
            </div>
            {d.reason && (
              <div className="mt-0.5 text-xs text-muted-foreground line-clamp-2">
                {d.reason}
              </div>
            )}
          </button>
        </li>
      ))}
    </ol>
  )
}

function SpanDetailPanel({ span }: { span: Span }) {
  const metadata = (span.metadata ?? {}) as Record<string, unknown>
  const decision = metadata['decision'] as
    | {
        decision_type?: string
        trigger?: Record<string, unknown>
        options?: Array<{ value: string; score: number; reason?: string }>
        selection?: { value?: string; confidence?: number; reason?: string }
        execution?: Record<string, unknown>
      }
    | undefined
  const tokenUsage = metadata['token_usage'] as
    | { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number; model?: string }
    | undefined
  const ragStats = metadata['rag_stats'] as
    | { hit_count?: number; top_score?: number; mode?: string }
    | undefined

  return (
    <>
      <SheetHeader>
        <SheetTitle className="font-mono">{span.name}</SheetTitle>
        <SheetDescription>
          {span.span_type} · {span.status} · {formatDuration(span.duration)}
        </SheetDescription>
      </SheetHeader>

      <div className="mt-4 space-y-4">
        {/* 基本信息 */}
        <Section title="基本信息">
          <Field label="span_id" value={span.span_id} mono />
          <Field label="parent_span_id" value={span.parent_span_id ?? '-'} mono />
          <Field label="span_type" value={span.span_type} />
          <Field label="start_time" value={formatTimestamp(span.start_time)} />
          <Field label="end_time" value={formatTimestamp(span.end_time)} />
        </Section>

        {/* 决策五元组（高亮） */}
        {decision && (
          <Section title="决策五元组（metadata.decision）" highlight>
            <Field label="decision_type" value={decision.decision_type ?? '-'} />
            {decision.trigger && (
              <Field
                label="trigger"
                value={JSON.stringify(decision.trigger, null, 2)}
                mono
                block
              />
            )}
            {decision.options && decision.options.length > 0 && (
              <div className="space-y-1">
                <div className="text-xs text-muted-foreground">options</div>
                <div className="space-y-1">
                  {decision.options.map((opt, i) => (
                    <div
                      key={i}
                      className="rounded border border-border/50 px-2 py-1 text-xs"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-mono">{opt.value}</span>
                        <span className="tabular-nums">{opt.score.toFixed(2)}</span>
                      </div>
                      {opt.reason && (
                        <div className="text-muted-foreground">{opt.reason}</div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
            {decision.selection && (
              <div className="space-y-1">
                <div className="text-xs text-muted-foreground">selection</div>
                <div className="rounded border border-emerald-500/30 bg-emerald-500/5 px-2 py-1.5 text-xs">
                  <div className="flex items-center justify-between">
                    <span className="font-mono">{decision.selection.value ?? '-'}</span>
                    <span className={`tabular-nums ${confidenceClass(decision.selection.confidence)}`}>
                      {decision.selection.confidence != null
                        ? decision.selection.confidence.toFixed(2)
                        : '-'}
                    </span>
                  </div>
                  {decision.selection.reason && (
                    <div className="mt-0.5 text-muted-foreground">
                      {decision.selection.reason}
                    </div>
                  )}
                </div>
              </div>
            )}
            {decision.execution && (
              <Field
                label="execution"
                value={JSON.stringify(decision.execution, null, 2)}
                mono
                block
              />
            )}
          </Section>
        )}

        {/* Token 使用 */}
        {tokenUsage && (
          <Section title="Token 使用（metadata.token_usage）">
            <Field label="model" value={tokenUsage.model ?? '-'} />
            <Field label="prompt_tokens" value={String(tokenUsage.prompt_tokens ?? 0)} mono />
            <Field
              label="completion_tokens"
              value={String(tokenUsage.completion_tokens ?? 0)}
              mono
            />
            <Field label="total_tokens" value={String(tokenUsage.total_tokens ?? 0)} mono />
          </Section>
        )}

        {/* RAG 统计 */}
        {ragStats && (
          <Section title="RAG 检索统计（metadata.rag_stats）">
            <Field label="hit_count" value={String(ragStats.hit_count ?? 0)} mono />
            <Field
              label="top_score"
              value={ragStats.top_score != null ? ragStats.top_score.toFixed(3) : '-'}
              mono
            />
            <Field label="mode" value={ragStats.mode ?? '-'} />
          </Section>
        )}

        {/* 输入输出 */}
        <Section title="输入数据">
          <pre className="overflow-x-auto rounded bg-muted/60 p-2 text-xs">
            {JSON.stringify(span.input_data ?? {}, null, 2)}
          </pre>
        </Section>
        <Section title="输出数据">
          <pre className="overflow-x-auto rounded bg-muted/60 p-2 text-xs">
            {JSON.stringify(span.output_data ?? {}, null, 2)}
          </pre>
        </Section>
      </div>
    </>
  )
}

function Section({
  title,
  highlight,
  children,
}: {
  title: string
  highlight?: boolean
  children: React.ReactNode
}) {
  return (
    <div
      className={
        highlight
          ? 'rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3'
          : 'rounded-lg border border-border/60 p-3'
      }
    >
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </div>
      <div className="space-y-1.5">{children}</div>
    </div>
  )
}

function Field({
  label,
  value,
  mono,
  block,
}: {
  label: string
  value: string
  mono?: boolean
  block?: boolean
}) {
  return (
    <div className={block ? 'flex flex-col gap-1' : 'flex items-baseline gap-2'}>
      <span className="min-w-32 text-xs text-muted-foreground">{label}</span>
      <span
        className={`text-sm ${mono ? 'font-mono' : ''} ${block ? 'whitespace-pre-wrap break-words' : 'truncate'}`}
      >
        {value}
      </span>
    </div>
  )
}

function formatTimestamp(value: number | null | undefined): string {
  if (value == null) return '-'
  if (value > 1e9) {
    // epoch seconds
    return new Date(value * 1000).toISOString()
  }
  return String(value)
}

function EmptyBlock({ description }: { description: string }) {
  return (
    <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
      {description}
    </div>
  )
}
