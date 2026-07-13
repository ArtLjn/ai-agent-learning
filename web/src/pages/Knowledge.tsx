import { useEffect, useMemo, useState } from 'react'
import {
  useDeleteKnowledge,
  useKnowledge,
  useKnowledgeEvaluation,
  useKnowledgeVersions,
  useRollbackKnowledge,
  useRunKnowledgeEvaluation,
  useUpdateKnowledgeText,
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
  PackageCheck,
  Pencil,
  RotateCcw,
  ShieldCheck,
  RefreshCw,
  SearchCheck,
  Send,
  TimerReset,
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

type KnowledgePublishStatus = 'pending' | 'ingesting' | 'published' | 'failed' | 'rolled_back'

type KnowledgePublishInfo = {
  status: KnowledgePublishStatus
  label: string
  reviewer: string
  publishedAt: string
  note: string
  source: 'metadata' | 'default'
}

const sampleDocs = [
  {
    title: '新员工入职与首日办公指南',
    category: 'inquiry',
    content:
      '新员工入职需确认钉钉、邮箱、SSO、VPN、办公设备、工牌门禁和常用系统入口是否准备完成。',
  },
  {
    title: '加班餐补与福利查询',
    category: 'billing',
    content: '加班餐补需要结合公司制度、办公地点、加班审批和考勤记录判断，异常时提供员工号、加班日期和审批单号。',
  },
  {
    title: '钉钉员工咨询机器人使用指南',
    category: 'inquiry',
    content:
      '钉钉机器人可查询制度、定位入口、说明审批材料、创建工单和提醒补充信息，敏感事项需转人工确认。',
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
  const [editTarget, setEditTarget] = useState<KnowledgeDocument | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editCategory, setEditCategory] = useState('')
  const [editContent, setEditContent] = useState('')
  const [versionTarget, setVersionTarget] = useState<KnowledgeDocument | null>(null)
  // 提示
  const [success, setSuccess] = useState('')
  const [error, setError] = useState('')
  const [uploadProgress, setUploadProgress] = useState<UploadProgressState | null>(null)

  const { data, isLoading, refetch, isFetching } = useKnowledge(page, PAGE_SIZE)
  const evaluation = useKnowledgeEvaluation()
  const runEvaluation = useRunKnowledgeEvaluation()
  const uploadText = useUploadKnowledgeText()
  const uploadFile = useUploadKnowledgeFile()
  const updateText = useUpdateKnowledgeText()
  const rollbackKnowledge = useRollbackKnowledge()
  const deleteMutation = useDeleteKnowledge()
  const versionsQuery = useKnowledgeVersions(versionTarget?.doc_id ?? null)

  const documents = useMemo(() => data?.documents || [], [data?.documents])
  const publishStats = useMemo(() => buildPublishStats(documents), [documents])
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

  const openEditDialog = (doc: KnowledgeDocument) => {
    setEditTarget(doc)
    setEditTitle(doc.title || doc.source || '')
    setEditCategory(doc.category || 'technical')
    setEditContent('')
  }

  const handleUpdateKnowledge = async () => {
    if (!editTarget || !editContent.trim()) return
    setError('')
    try {
      const result = await updateText.mutateAsync({
        docId: editTarget.doc_id,
        data: {
          title: editTitle,
          category: editCategory,
          content: editContent,
        },
      })
      flashSuccess(`已发布新版本：v${String(result.version ?? '-')}`)
      setEditTarget(null)
      setEditContent('')
    } catch (err) {
      setError(err instanceof ApiError ? err.detail || err.message : '更新失败')
    }
  }

  const handleRollback = async (version: number) => {
    if (!versionTarget) return
    setError('')
    try {
      const result = await rollbackKnowledge.mutateAsync({
        docId: versionTarget.doc_id,
        version,
      })
      flashSuccess(`已回滚并发布新版本：v${String(result.version ?? '-')}`)
      setVersionTarget(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.detail || err.message : '回滚失败')
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

      <KnowledgePublishPanel
        documents={documents}
        total={total}
        stats={publishStats}
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
                        onEdit={() => openEditDialog(doc)}
                        onRollback={() => setVersionTarget(doc)}
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

      <Dialog open={editTarget !== null} onOpenChange={(v) => !v && setEditTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>更新知识文档</DialogTitle>
            <DialogDescription>
              提交后会重新入库并生成新的发布版本，历史版本会保留。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-muted-foreground">文档标题</label>
              <Input value={editTitle} onChange={(e) => setEditTitle(e.target.value)} className="mt-1" />
            </div>
            <div>
              <label className="text-xs text-muted-foreground">分类</label>
              <Input value={editCategory} onChange={(e) => setEditCategory(e.target.value)} className="mt-1" />
            </div>
            <div>
              <label className="text-xs text-muted-foreground">新版本内容</label>
              <Textarea
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                rows={8}
                placeholder="粘贴更新后的处理手册、FAQ 或业务规则"
                className="mt-1"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditTarget(null)}>
              取消
            </Button>
            <Button
              onClick={handleUpdateKnowledge}
              disabled={!editContent.trim() || updateText.isPending}
            >
              <Pencil className="mr-1.5 h-4 w-4" />
              发布新版本
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={versionTarget !== null} onOpenChange={(v) => !v && setVersionTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>版本回滚</DialogTitle>
            <DialogDescription>
              选择一个历史版本作为内容来源，系统会创建新的 active 版本。
            </DialogDescription>
          </DialogHeader>
          {versionsQuery.isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-12 rounded-md" />
              <Skeleton className="h-12 rounded-md" />
            </div>
          ) : versionsQuery.error ? (
            <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning">
              版本加载失败：{versionsQuery.error.message}
            </div>
          ) : (
            <div className="space-y-2">
              {(versionsQuery.data?.versions ?? []).map((version) => (
                <div
                  key={version.version}
                  className="flex items-center justify-between gap-3 rounded-md border border-border bg-background p-3"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-sm font-medium">
                      <span>v{version.version}</span>
                      {version.is_active && (
                        <Badge variant="outline" className="border-success/30 bg-success/10 text-success">
                          当前
                        </Badge>
                      )}
                    </div>
                    <div className="mt-1 line-clamp-1 text-xs text-muted-foreground">
                      {version.content || '该版本没有可预览文本'}
                    </div>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={version.is_active || !version.content || rollbackKnowledge.isPending}
                    onClick={() => handleRollback(version.version)}
                  >
                    <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
                    回滚
                  </Button>
                </div>
              ))}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setVersionTarget(null)}>
              关闭
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

function KnowledgePublishPanel({
  documents,
  total,
  stats,
}: {
  documents: KnowledgeDocument[]
  total: number
  stats: ReturnType<typeof buildPublishStats>
}) {
  const recent = documents
    .slice()
    .sort((a, b) => (b.ingested_at || '').localeCompare(a.ingested_at || ''))
    .slice(0, 4)

  return (
    <Card className="bg-card border-border">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-sm">
              <ShieldCheck className="h-4 w-4 text-primary" />
              知识入库审核与发布
            </CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              展示文档从提交、审核到发布的状态；当前接口未返回审核字段时按已发布元数据展示
            </p>
          </div>
          <Badge variant="outline" className="border-primary/30 bg-primary/10 text-primary">
            A-08 发布看板
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-4 gap-3">
          <PublishMetric
            label="总文档"
            value={String(total)}
            icon={BookOpen}
            tone="primary"
          />
          <PublishMetric
            label="待审核"
            value={String(stats.pending)}
            icon={TimerReset}
            tone={stats.pending > 0 ? 'warning' : 'muted'}
          />
          <PublishMetric
            label="已发布"
            value={String(stats.published)}
            icon={PackageCheck}
            tone="success"
          />
          <PublishMetric
            label="可检索分块"
            value={String(stats.chunks)}
            icon={Layers}
            tone="primary"
          />
        </div>

        <div className="mt-4 grid grid-cols-[1.25fr_1fr] gap-4">
          <div className="rounded-md border border-border bg-background p-3">
            <div className="mb-3 flex items-center justify-between gap-2">
              <div className="text-xs font-medium text-muted-foreground">发布流程</div>
              <Badge variant="outline" className="border-0 bg-secondary text-[10px]">
                只读演示
              </Badge>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <PublishStage
                index="01"
                title="提交入库"
                detail="文本或文件进入 rag-service 解析队列"
                active={total > 0}
              />
              <PublishStage
                index="02"
                title="审核确认"
                detail="管理员核对来源、分类、分块质量"
                active={stats.pending > 0}
                warning={stats.pending > 0}
              />
              <PublishStage
                index="03"
                title="发布检索"
                detail="写入 Qdrant 后供工单处理引用"
                active={stats.published > 0}
                success={stats.published > 0}
              />
            </div>
          </div>

          <div className="rounded-md border border-border bg-background p-3">
            <div className="mb-3 text-xs font-medium text-muted-foreground">最近发布</div>
            {recent.length === 0 ? (
              <div className="flex h-[94px] items-center justify-center text-xs text-muted-foreground">
                暂无文档发布记录
              </div>
            ) : (
              <div className="space-y-2">
                {recent.map((doc) => {
                  const publish = getKnowledgePublishInfo(doc)
                  return (
                    <div key={doc.doc_id} className="flex items-center gap-2 text-xs">
                      <span className={publishDotClass(publish.status)} />
                      <span className="min-w-0 flex-1 truncate text-foreground">
                        {doc.source || doc.doc_id}
                      </span>
                      <span className="shrink-0 text-muted-foreground">
                        {formatDateTime(publish.publishedAt || doc.ingested_at)}
                      </span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function PublishMetric({
  label,
  value,
  icon: Icon,
  tone,
}: {
  label: string
  value: string
  icon: typeof BookOpen
  tone: 'primary' | 'success' | 'warning' | 'muted'
}) {
  const toneClass = {
    primary: 'text-primary bg-primary/10',
    success: 'text-success bg-success/10',
    warning: 'text-warning bg-warning/10',
    muted: 'text-muted-foreground bg-secondary',
  }[tone]

  return (
    <div className="rounded-md border border-border bg-background px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[11px] text-muted-foreground">{label}</div>
        <span className={`rounded p-1 ${toneClass}`}>
          <Icon className="h-3.5 w-3.5" />
        </span>
      </div>
      <div className="mt-1 font-mono text-lg font-semibold">{value}</div>
    </div>
  )
}

function PublishStage({
  index,
  title,
  detail,
  active,
  warning,
  success,
}: {
  index: string
  title: string
  detail: string
  active: boolean
  warning?: boolean
  success?: boolean
}) {
  const statusClass = success
    ? 'border-success/35 bg-success/5'
    : warning
      ? 'border-warning/35 bg-warning/5'
      : active
        ? 'border-primary/35 bg-primary/5'
        : 'border-border bg-card'

  return (
    <div className={`min-h-[92px] rounded-md border p-3 ${statusClass}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[10px] text-muted-foreground">{index}</span>
        {success ? (
          <CheckCircle2 className="h-3.5 w-3.5 text-success" />
        ) : warning ? (
          <TimerReset className="h-3.5 w-3.5 text-warning" />
        ) : (
          <Send className="h-3.5 w-3.5 text-primary" />
        )}
      </div>
      <div className="mt-2 text-sm font-medium">{title}</div>
      <div className="mt-1 text-[11px] leading-4 text-muted-foreground">{detail}</div>
    </div>
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

function buildPublishStats(documents: KnowledgeDocument[]) {
  return documents.reduce(
    (acc, doc) => {
      const publish = getKnowledgePublishInfo(doc)
      acc[publish.status] += 1
      acc.chunks += doc.chunk_count || 0
      return acc
    },
    { pending: 0, ingesting: 0, published: 0, failed: 0, rolled_back: 0, chunks: 0 }
  )
}

function getKnowledgePublishInfo(doc: KnowledgeDocument): KnowledgePublishInfo {
  const extra = doc.extra ?? {}
  const rawStatus = String(
    doc.status ??
    extra.publish_status ??
    extra.review_status ??
    extra.status ??
    ''
  ).toLowerCase()
  const status: KnowledgePublishStatus =
    rawStatus === 'pending' || rawStatus === 'reviewing'
      ? 'pending'
      : rawStatus === 'ingesting'
        ? 'ingesting'
        : rawStatus === 'failed'
          ? 'failed'
          : rawStatus === 'rolled_back'
            ? 'rolled_back'
            : 'published'

  return {
    status,
    label: status === 'pending'
      ? '待审核'
      : status === 'ingesting'
        ? '入库中'
        : status === 'failed'
          ? '入库失败'
          : status === 'rolled_back'
            ? '已回滚'
            : '已发布',
    reviewer: String(extra.reviewer ?? extra.published_by ?? extra.approved_by ?? 'system'),
    publishedAt: String(extra.published_at ?? extra.reviewed_at ?? doc.ingested_at ?? ''),
    note: String(extra.publish_note ?? extra.review_note ?? extra.note ?? '已写入向量库，可被工单处理流程检索引用'),
    source: rawStatus ? 'metadata' : 'default',
  }
}

function publishBadgeClass(status: KnowledgePublishStatus): string {
  if (status === 'pending') return 'border-warning/30 bg-warning/10 text-warning'
  if (status === 'ingesting') return 'border-primary/30 bg-primary/10 text-primary'
  if (status === 'failed') return 'border-destructive/30 bg-destructive/10 text-destructive'
  if (status === 'rolled_back') return 'border-warning/30 bg-warning/10 text-warning'
  return 'border-success/30 bg-success/10 text-success'
}

function publishDotClass(status: KnowledgePublishStatus): string {
  if (status === 'pending') return 'h-2 w-2 shrink-0 rounded-full bg-warning'
  if (status === 'ingesting') return 'h-2 w-2 shrink-0 rounded-full bg-primary'
  if (status === 'failed') return 'h-2 w-2 shrink-0 rounded-full bg-destructive'
  if (status === 'rolled_back') return 'h-2 w-2 shrink-0 rounded-full bg-warning'
  return 'h-2 w-2 shrink-0 rounded-full bg-success'
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
  onEdit,
  onRollback,
  onDelete,
  deleting,
}: {
  doc: KnowledgeDocument
  onEdit: () => void
  onRollback: () => void
  onDelete: () => void
  deleting: boolean
}) {
  const publish = getKnowledgePublishInfo(doc)

  return (
    <div className="group flex items-start gap-3 rounded-md border border-border bg-background p-3 transition-colors hover:border-primary/50">
      <FileText className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1 truncate text-sm font-medium">
            {doc.title || doc.source || '(无 source)'}
          </div>
          <Badge variant="outline" className="shrink-0 border-0 bg-primary/15 px-1.5 py-0 text-[10px] text-primary">
            {doc.category || 'uncategorized'}
          </Badge>
          {doc.version && (
            <Badge variant="outline" className="shrink-0 border-0 bg-secondary px-1.5 py-0 text-[10px]">
              v{doc.version}
            </Badge>
          )}
          <Badge variant="outline" className={`shrink-0 px-1.5 py-0 text-[10px] ${publishBadgeClass(publish.status)}`}>
            {publish.label}
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
          <span>审核人: {publish.reviewer}</span>
          {publish.source === 'default' && <span>演示默认发布</span>}
        </div>
        <div className="mt-1 line-clamp-1 text-[11px] text-muted-foreground">
          {publish.note}
        </div>
      </div>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 w-7 p-0 text-muted-foreground opacity-0 hover:text-primary group-hover:opacity-100"
        onClick={onEdit}
        title="更新"
      >
        <Pencil className="h-3.5 w-3.5" />
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 w-7 p-0 text-muted-foreground opacity-0 hover:text-warning group-hover:opacity-100"
        onClick={onRollback}
        title="回滚"
      >
        <RotateCcw className="h-3.5 w-3.5" />
      </Button>
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
