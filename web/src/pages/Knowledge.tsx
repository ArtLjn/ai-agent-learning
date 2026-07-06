import { useEffect, useMemo, useState } from 'react'
import {
  useDeleteKnowledge,
  useKnowledge,
  useKnowledgeEvaluation,
  useRunKnowledgeEvaluation,
  useUploadKnowledgeFile,
  useUploadKnowledgeText,
} from '@/hooks/useApi'
import { ApiError } from '@/lib/api'
import type { KnowledgeDocument, KnowledgeEvaluationReport } from '@/types'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  AlertCircle,
  BookOpen,
  BarChart3,
  CheckCircle2,
  FileText,
  Layers,
  RefreshCw,
  SearchCheck,
  Trash2,
  Upload,
} from 'lucide-react'

const PAGE_SIZE = 20

type UploadProgressState = {
  kind: 'text' | 'file'
  percent: number
  status: 'running' | 'success' | 'error'
  fileName?: string
}

const sampleDocs = [
  {
    title: '系统崩溃排查手册',
    category: 'technical',
    content:
      '系统崩溃常见原因：1. 内存不足 2. 数据库连接池耗尽 3. 磁盘空间满。处理时先查看服务端日志，再检查资源占用和依赖服务状态。',
  },
  {
    title: '退款流程说明',
    category: 'billing',
    content: '退款流程：1. 用户提交退款申请 2. 客服审核订单状态 3. 财务确认退款金额 4. 3-5 个工作日到账。',
  },
  {
    title: 'VIP 用户服务协议',
    category: 'complaint',
    content:
      'VIP 用户享有优先处理权。投诉工单应在 2 小时内响应，处理结果需要记录回访状态和满意度。',
  },
]

export function Knowledge() {
  const [page, setPage] = useState(1)
  // text 模式表单
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [category, setCategory] = useState('technical')
  // file 模式表单
  const [fileTitle, setFileTitle] = useState('')
  const [fileCategory, setFileCategory] = useState('technical')
  const [file, setFile] = useState<File | null>(null)
  // 删除确认弹窗
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeDocument | null>(null)
  // 提示
  const [success, setSuccess] = useState('')
  const [error, setError] = useState('')
  const [uploadProgress, setUploadProgress] = useState<UploadProgressState | null>(null)

  const { data, isLoading, refetch, isFetching } = useKnowledge(page, PAGE_SIZE)
  const evaluation = useKnowledgeEvaluation()
  const runEvaluation = useRunKnowledgeEvaluation()
  const uploadText = useUploadKnowledgeText()
  const uploadFile = useUploadKnowledgeFile()
  const deleteMutation = useDeleteKnowledge()

  const documents = useMemo(() => data?.documents || [], [data?.documents])
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const evaluationReport = evaluation.data?.report ?? null
  const isUploading =
    uploadProgress?.status === 'running' || uploadText.isPending || uploadFile.isPending

  const handleRunEvaluation = async () => {
    setError('')
    try {
      await runEvaluation.mutateAsync()
      flashSuccess('RAG 召回评测已完成')
    } catch (err) {
      setError(err instanceof ApiError ? err.detail || err.message : '评测失败，请稍后重试')
    }
  }

  useEffect(() => {
    if (uploadProgress?.status !== 'running') return

    const timer = window.setInterval(() => {
      setUploadProgress((current) => {
        if (!current || current.status !== 'running') return current
        const step = current.kind === 'file' ? 3 : 6
        const nextPercent = Math.min(92, current.percent + step)
        return { ...current, percent: nextPercent }
      })
    }, 900)

    return () => window.clearInterval(timer)
  }, [uploadProgress?.status])

  const flashSuccess = (msg: string) => {
    setSuccess(msg)
    setTimeout(() => setSuccess(''), 3000)
  }

  const startUploadProgress = (kind: 'text' | 'file', fileName?: string) => {
    setUploadProgress({ kind, fileName, percent: 8, status: 'running' })
  }

  const finishUploadProgress = () => {
    setUploadProgress((current) =>
      current ? { ...current, percent: 100, status: 'success' } : current
    )
    setTimeout(() => {
      setUploadProgress((current) => (current?.status === 'success' ? null : current))
    }, 1800)
  }

  const failUploadProgress = () => {
    setUploadProgress((current) =>
      current
        ? { ...current, percent: Math.max(current.percent, 86), status: 'error' }
        : current
    )
  }

  const handleUploadText = async () => {
    if (!content.trim()) return
    setError('')
    startUploadProgress('text')
    try {
      const result = await uploadText.mutateAsync({ title, content, category })
      finishUploadProgress()
      flashSuccess(
        `已入库：${result.chunk_count ?? 0} 个分块（action: ${result.action ?? 'unknown'}）`
      )
      setTitle('')
      setContent('')
    } catch (err) {
      failUploadProgress()
      setError(err instanceof ApiError ? err.detail || err.message : '上传失败，请稍后重试')
    }
  }

  const handleUploadFile = async () => {
    if (!file) return
    setError('')
    startUploadProgress('file', file.name)
    try {
      const formData = new FormData()
      formData.append('file', file)
      if (fileTitle) formData.append('title', fileTitle)
      formData.append('category', fileCategory)
      const result = await uploadFile.mutateAsync(formData)
      finishUploadProgress()
      flashSuccess(
        `${file.name} 已入库：${result.chunk_count ?? 0} 个分块（action: ${result.action ?? 'unknown'}）`
      )
      setFile(null)
      setFileTitle('')
    } catch (err) {
      failUploadProgress()
      setError(err instanceof ApiError ? err.detail || err.message : '上传失败，请稍后重试')
    }
  }

  const handleConfirmDelete = async () => {
    if (!deleteTarget) return
    setError('')
    try {
      await deleteMutation.mutateAsync(deleteTarget.doc_id)
      flashSuccess(`已删除：${deleteTarget.source || deleteTarget.doc_id.slice(0, 8)}`)
      setDeleteTarget(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail || err.message : '删除失败')
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-primary" />
            知识库管理
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            管理 rag-service ticket_knowledge collection（PDF/MD/TXT 文件 + 文本入库）
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${isFetching ? 'animate-spin' : ''}`} />
          刷新
        </Button>
      </div>

      <EvaluationPanel
        report={evaluationReport}
        available={evaluation.data?.available ?? false}
        loading={evaluation.isLoading}
        running={runEvaluation.isPending}
        onRun={handleRunEvaluation}
      />

      <div className="grid grid-cols-12 gap-4">
        {/* 左栏：上传 */}
        <div className="col-span-5 space-y-4">
          <Card className="bg-card border-border">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">上传新文档</CardTitle>
            </CardHeader>
            <CardContent>
              <Tabs defaultValue="text">
                <TabsList className="grid w-full grid-cols-2">
                  <TabsTrigger value="text">粘贴文本</TabsTrigger>
                  <TabsTrigger value="file">上传文件</TabsTrigger>
                </TabsList>
                <TabsContent value="text" className="space-y-4 pt-4">
                  <div>
                    <label className="text-xs text-muted-foreground">文档标题（可选）</label>
                    <Input
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                      placeholder="不填则用内容前 30 字"
                      className="mt-1"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">分类</label>
                    <Input
                      value={category}
                      onChange={(e) => setCategory(e.target.value)}
                      placeholder="technical"
                      className="mt-1"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">内容（markdown / plain text）</label>
                    <Textarea
                      value={content}
                      onChange={(e) => setContent(e.target.value)}
                      placeholder="# 工单排查手册&#10;步骤 1. ..."
                      rows={8}
                      className="mt-1"
                    />
                  </div>
                  <Button
                    onClick={handleUploadText}
                    disabled={!content.trim() || isUploading}
                  >
                    <Upload className="w-4 h-4 mr-1.5" />
                    {uploadText.isPending ? '入库中' : '上传文本'}
                  </Button>
                </TabsContent>
                <TabsContent value="file" className="space-y-4 pt-4">
                  <div>
                    <label className="text-xs text-muted-foreground">文档标题（可选）</label>
                    <Input
                      value={fileTitle}
                      onChange={(e) => setFileTitle(e.target.value)}
                      placeholder="不填则用文件名"
                      className="mt-1"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">分类</label>
                    <Input
                      value={fileCategory}
                      onChange={(e) => setFileCategory(e.target.value)}
                      placeholder="technical"
                      className="mt-1"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">文件（PDF/MD/TXT）</label>
                    <Input
                      type="file"
                      accept=".pdf,.md,.markdown,.txt"
                      onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                      className="mt-1 file:mr-3 file:rounded file:border-0 file:bg-primary file:px-3 file:py-1 file:text-primary-foreground hover:file:bg-primary/90"
                    />
                    {file && (
                      <div className="mt-2 text-xs text-muted-foreground">
                        已选：{file.name}（{(file.size / 1024).toFixed(1)} KB）
                      </div>
                    )}
                  </div>
                  <Button
                    onClick={handleUploadFile}
                    disabled={!file || isUploading}
                  >
                    <Upload className="w-4 h-4 mr-1.5" />
                    {uploadFile.isPending ? '入库中' : '上传文件'}
                  </Button>
                </TabsContent>
              </Tabs>

              {uploadProgress && (
                <UploadProgress
                  progress={uploadProgress}
                  onRefresh={() => refetch()}
                />
              )}

              {success && (
                <div className="mt-3 flex items-center gap-1.5 text-success text-sm">
                  <CheckCircle2 className="w-4 h-4" />
                  {success}
                </div>
              )}
              {error && (
                <div className="mt-3 flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>{error}</span>
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="bg-card border-border">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">快速填充（粘贴文本 tab）</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {sampleDocs.map((doc) => (
                <button
                  key={doc.title}
                  type="button"
                  onClick={() => {
                    setTitle(doc.title)
                    setContent(doc.content)
                    setCategory(doc.category)
                  }}
                  className="w-full rounded-md border border-border bg-background p-3 text-left transition-colors hover:border-primary/50"
                >
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium">{doc.title}</span>
                    <Badge variant="outline" className="border-0 bg-primary/15 text-[10px] text-primary">
                      {doc.category}
                    </Badge>
                  </div>
                  <p className="line-clamp-2 text-xs text-muted-foreground">{doc.content}</p>
                </button>
              ))}
            </CardContent>
          </Card>
        </div>

        {/* 右栏：列表 */}
        <div className="col-span-7">
          <Card className="bg-card border-border">
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="text-sm">现有文档</CardTitle>
                <div className="flex items-center gap-3">
                  <Badge variant="outline" className="border-0 bg-secondary text-xs">
                    共 {total} 篇
                  </Badge>
                  <div className="flex items-center gap-1 text-xs">
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 px-2"
                      disabled={page <= 1 || isFetching}
                      onClick={() => setPage((p) => Math.max(1, p - 1))}
                    >
                      上一页
                    </Button>
                    <span className="text-muted-foreground">
                      {page} / {totalPages}
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 px-2"
                      disabled={page >= totalPages || isFetching}
                      onClick={() => setPage((p) => p + 1)}
                    >
                      下一页
                    </Button>
                  </div>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[640px] pr-3">
                {isLoading ? (
                  <div className="space-y-2">
                    {Array.from({ length: 8 }).map((_, index) => (
                      <Skeleton key={index} className="h-16 rounded-md" />
                    ))}
                  </div>
                ) : documents.length === 0 ? (
                  <div className="py-16 text-center text-sm text-muted-foreground">
                    暂无文档，从左侧上传第一篇
                  </div>
                ) : (
                  <div className="space-y-2">
                    {documents.map((doc) => (
                      <KnowledgeListItem
                        key={doc.doc_id}
                        doc={doc}
                        onDelete={() => setDeleteTarget(doc)}
                        deleting={
                          deleteMutation.isPending && deleteTarget?.doc_id === doc.doc_id
                        }
                      />
                    ))}
                  </div>
                )}
              </ScrollArea>
            </CardContent>
          </Card>
        </div>
      </div>

      <Dialog open={deleteTarget !== null} onOpenChange={(v) => !v && setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认删除文档</DialogTitle>
            <DialogDescription>
              将从 rag-service ticket_knowledge collection 永久删除，Qdrant points 与
              SQLite metadata 同时清除，无法恢复。
            </DialogDescription>
          </DialogHeader>
          {deleteTarget && (
            <div className="rounded-md border border-border bg-background p-3 text-sm">
              <div className="flex items-center gap-2">
                <FileText className="w-4 h-4 text-primary" />
                <span className="font-medium">{deleteTarget.source || '(无 source)'}</span>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground">
                <div>doc_id: <span className="font-mono">{deleteTarget.doc_id.slice(0, 12)}…</span></div>
                <div>分类: {deleteTarget.category || '-'}</div>
                <div>分块数: {deleteTarget.chunk_count}</div>
                <div>入库时间: {deleteTarget.ingested_at?.slice(0, 19).replace('T', ' ') || '-'}</div>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>
              取消
            </Button>
            <Button
              variant="destructive"
              onClick={handleConfirmDelete}
              disabled={deleteMutation.isPending}
            >
              <Trash2 className="w-4 h-4 mr-1.5" />
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function UploadProgress({
  progress,
  onRefresh,
}: {
  progress: UploadProgressState
  onRefresh: () => void
}) {
  const stage = getUploadStage(progress)
  const barClass =
    progress.status === 'error'
      ? 'bg-warning'
      : progress.status === 'success'
        ? 'bg-success'
        : 'bg-primary'

  return (
    <div className="mt-4 rounded-md border border-border bg-background px-3 py-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{stage.title}</div>
          <div className="mt-0.5 truncate text-xs text-muted-foreground">{stage.detail}</div>
        </div>
        <span className="shrink-0 font-mono text-xs text-muted-foreground">
          {Math.round(progress.percent)}%
        </span>
      </div>
      <div
        className="h-2 w-full overflow-hidden rounded-full bg-secondary"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progress.percent)}
        aria-label="知识库入库进度"
      >
        <div
          className={`h-full rounded-full transition-all duration-700 ease-out ${barClass}`}
          style={{ width: `${progress.percent}%` }}
        />
      </div>
      {progress.status === 'error' && (
        <div className="mt-2 flex items-center justify-between gap-3 text-xs text-muted-foreground">
          <span>远端可能仍在后台处理，可刷新列表确认是否已经入库。</span>
          <Button variant="outline" size="sm" className="h-7 px-2" onClick={onRefresh}>
            <RefreshCw className="mr-1 h-3 w-3" />
            刷新列表
          </Button>
        </div>
      )}
    </div>
  )
}

function EvaluationPanel({
  report,
  available,
  loading,
  running,
  onRun,
}: {
  report: KnowledgeEvaluationReport | null
  available: boolean
  loading: boolean
  running: boolean
  onRun: () => void
}) {
  const metrics = report?.summary.metrics
  const recall = metrics?.recall_at_k ?? {}
  const failedSamples = report?.samples.filter((sample) => !sample.hit).slice(0, 3) ?? []

  return (
    <Card className="bg-card border-border">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-sm">
              <BarChart3 className="h-4 w-4 text-primary" />
              RAG 召回评测
            </CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              基于 golden set 计算 Recall@K，用来衡量知识库是否能召回标准答案片段
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={onRun}
            disabled={running}
          >
            <SearchCheck className={`mr-1.5 h-3.5 w-3.5 ${running ? 'animate-pulse' : ''}`} />
            {running ? '评测中' : '运行评测'}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="grid grid-cols-5 gap-3">
            {Array.from({ length: 5 }).map((_, index) => (
              <Skeleton key={index} className="h-16 rounded-md" />
            ))}
          </div>
        ) : !available || !report ? (
          <div className="rounded-md border border-dashed border-border bg-background p-4 text-sm text-muted-foreground">
            暂无评测报告。点击“运行评测”后会显示 Recall@1/3/5/10、MRR 和未命中样本。
          </div>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-6 gap-3">
              <MetricTile label="样本数" value={String(report.summary.sample_count)} />
              <MetricTile label="Recall@1" value={formatPercent(recall['1'])} />
              <MetricTile label="Recall@3" value={formatPercent(recall['3'])} />
              <MetricTile label="Recall@5" value={formatPercent(recall['5'])} />
              <MetricTile label="MRR" value={formatNumber(metrics?.mrr)} />
              <MetricTile label="Hit Rate" value={formatPercent(metrics?.hit_rate)} />
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>模式：{report.mode}</span>
              <span>top_k：{report.top_k}</span>
              <span>数据集：{report.dataset_path}</span>
              <span>完成：{formatDateTime(report.finished_at)}</span>
            </div>
            {failedSamples.length > 0 && (
              <div className="rounded-md border border-warning/25 bg-warning/5 p-3">
                <div className="mb-2 text-xs font-medium text-warning">
                  未命中样本（前 {failedSamples.length} 条）
                </div>
                <div className="space-y-1.5">
                  {failedSamples.map((sample) => (
                    <div key={sample.query} className="text-xs text-muted-foreground">
                      <span className="text-foreground">{sample.query}</span>
                      <span className="ml-2 font-mono">
                        relevant: {sample.relevant.slice(0, 2).join(', ')}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function MetricTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-background px-3 py-2">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="mt-1 font-mono text-lg font-semibold">{value}</div>
    </div>
  )
}

function formatPercent(value: number | undefined) {
  if (value == null) return '-'
  return `${(value * 100).toFixed(1)}%`
}

function formatNumber(value: number | undefined) {
  if (value == null) return '-'
  return value.toFixed(3)
}

function formatDateTime(value: string | undefined) {
  if (!value) return '-'
  return value.slice(0, 19).replace('T', ' ')
}

function getUploadStage(progress: UploadProgressState) {
  if (progress.status === 'success') {
    return {
      title: '入库完成',
      detail: '已写入 rag-service，正在刷新文档列表。',
    }
  }

  if (progress.status === 'error') {
    return {
      title: '入库请求未完成',
      detail: '网络超时或服务返回错误，文档可能仍在远端继续处理。',
    }
  }

  if (progress.percent < 28) {
    return {
      title: progress.kind === 'file' ? '正在上传文件' : '正在提交文本',
      detail: progress.fileName ? `文件：${progress.fileName}` : '正在发送到主系统 API。',
    }
  }

  if (progress.percent < 62) {
    return {
      title: 'RAG 解析中',
      detail: progress.kind === 'file'
        ? 'PDF/MD/TXT 会经过解析、清洗与分块，稍等一下。'
        : '文本正在清洗、分块并准备向量化。',
    }
  }

  if (progress.percent < 90) {
    return {
      title: '向量化入库中',
      detail: 'rag-service 正在生成 Embedding 并写入 Qdrant。',
    }
  }

  return {
    title: '等待服务返回',
    detail: '长文档可能需要几十秒，完成后会自动刷新列表。',
  }
}

function KnowledgeListItem({
  doc,
  onDelete,
  deleting,
}: {
  doc: KnowledgeDocument
  onDelete: () => void
  deleting: boolean
}) {
  return (
    <div className="group flex items-start gap-3 rounded-md border border-border bg-background p-3 transition-colors hover:border-primary/50">
      <FileText className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1 truncate text-sm font-medium">
            {doc.source || '(无 source)'}
          </div>
          <Badge variant="outline" className="shrink-0 border-0 bg-primary/15 px-1.5 py-0 text-[10px] text-primary">
            {doc.category || 'uncategorized'}
          </Badge>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground/80">
          <span className="flex items-center gap-0.5">
            <Layers className="h-2.5 w-2.5" />
            {doc.chunk_count} 块
          </span>
          <span className="font-mono">{doc.doc_id.slice(0, 10)}</span>
          {doc.ingested_at && (
            <span>{doc.ingested_at.slice(0, 19).replace('T', ' ')}</span>
          )}
        </div>
      </div>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 w-7 p-0 text-muted-foreground opacity-0 hover:text-destructive group-hover:opacity-100"
        onClick={onDelete}
        disabled={deleting}
        title="删除"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </Button>
    </div>
  )
}
