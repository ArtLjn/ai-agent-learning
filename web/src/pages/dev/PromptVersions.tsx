import { useEffect, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { Loader2, Sparkles, FileText, GitCompare, RefreshCw } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import { toast } from '@/lib/toast'
import type { PromptAgentName, PromptVersion } from '@/types'

const AGENTS: { id: PromptAgentName; label: string; description: string }[] = [
  { id: 'intent', label: 'Intent', description: 'TicketIntentAgent — 自然语言理解工单意图' },
  { id: 'classify', label: 'Classifier', description: 'ClassifierAgent — 工单分类与优先级' },
  { id: 'process', label: 'Processor', description: 'ReActProcessorAgent — ReAct 工单处理' },
  { id: 'review', label: 'Reviewer', description: 'ReviewerAgent — 处理结果质量审核' },
  { id: 'coordinator', label: 'Coordinator', description: 'CoordinatorAgent — 协调与升级决策' },
]

export function PromptVersions() {
  const [agent, setAgent] = useState<PromptAgentName>('classify')
  const [versions, setVersions] = useState<PromptVersion[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 创建对话框
  const [createOpen, setCreateOpen] = useState(false)
  const [newTemplate, setNewTemplate] = useState('')
  const [newNote, setNewNote] = useState('')
  const [creating, setCreating] = useState(false)

  // diff 对话框
  const [diffOpen, setDiffOpen] = useState(false)
  const [diffFrom, setDiffFrom] = useState<number | null>(null)
  const [diffTo, setDiffTo] = useState<number | null>(null)
  const [diffText, setDiffText] = useState('')
  const [diffLoading, setDiffLoading] = useState(false)

  // 热重载
  const [reloading, setReloading] = useState(false)

  async function loadVersions(target: PromptAgentName) {
    setLoading(true)
    setError(null)
    try {
      const resp = await api.listPromptVersions(target)
      setVersions(resp.items)
    } catch (e) {
      setError(e instanceof ApiError ? e.detail || e.message : String(e))
      setVersions([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    queueMicrotask(() => {
      void loadVersions(agent)
    })
  }, [agent])

  async function reloadActive(messageOnSuccess: string) {
    setReloading(true)
    try {
      const resp = await api.reloadActivePrompts()
      const reloadedCount = Object.keys(resp.reloaded).length
      toast.success(
        'Prompt 已热重载',
        `${messageOnSuccess}（生效 ${reloadedCount} 个 Agent${
          resp.skipped.length ? `，跳过 ${resp.skipped.length} 个` : ''
        }）`,
      )
    } catch (e) {
      toast.error(
        '热重载失败',
        e instanceof ApiError ? e.detail || e.message : String(e),
      )
    } finally {
      setReloading(false)
    }
  }

  async function submitCreate() {
    if (!newTemplate.trim()) return
    setCreating(true)
    setError(null)
    try {
      await api.createPromptVersion(agent, {
        template: newTemplate,
        note: newNote || undefined,
        activate: true,
      })
      setCreateOpen(false)
      setNewTemplate('')
      setNewNote('')
      await loadVersions(agent)
      // 新建并自动 activate → 热重载让新 prompt 立刻生效
      await reloadActive(`新版本已激活并热重载到 ${agent}`)
    } catch (e) {
      setError(e instanceof ApiError ? e.detail || e.message : String(e))
    } finally {
      setCreating(false)
    }
  }

  async function activate(version: number) {
    setError(null)
    try {
      await api.activatePromptVersion(agent, version)
      await loadVersions(agent)
      // 激活后热重载
      await reloadActive(`${agent} v${version} 已激活并热重载`)
    } catch (e) {
      setError(e instanceof ApiError ? e.detail || e.message : String(e))
    }
  }

  async function openDiff(toVersion: number) {
    if (versions.length < 2) return
    // 默认拿当前 active 作为 from
    const active = versions.find((v) => v.is_active)
    const from = active ? active.version : versions.find((v) => v.version !== toVersion)!.version
    setDiffFrom(from)
    setDiffTo(toVersion)
    setDiffOpen(true)
    setDiffLoading(true)
    setError(null)
    try {
      const resp = await api.diffPromptVersions(agent, from, toVersion)
      setDiffText(resp.diff || '(no diff)')
    } catch (e) {
      setError(e instanceof ApiError ? e.detail || e.message : String(e))
    } finally {
      setDiffLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-primary" />
            Prompt 版本管理
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            5 个 Agent 的 system prompt 多版本管理 + 激活切换
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={() => reloadActive('已手动热重载 active 版本')}
            disabled={reloading || loading}
          >
            {reloading ? (
              <Loader2 className="w-4 h-4 mr-1.5 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4 mr-1.5" />
            )}
            热重载
          </Button>
          <Button onClick={() => setCreateOpen(true)} disabled={loading}>
            <FileText className="w-4 h-4 mr-1.5" />
            新建版本
          </Button>
        </div>
      </div>

      <Tabs value={agent} onValueChange={(v) => setAgent(v as PromptAgentName)}>
        <TabsList>
          {AGENTS.map((a) => (
            <TabsTrigger key={a.id} value={a.id}>
              {a.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {error && (
        <div className="px-3 py-2 rounded border border-red-500/30 bg-red-500/10 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="grid gap-3">
        {AGENTS.find((a) => a.id === agent)?.description && (
          <p className="text-xs text-muted-foreground">
            {AGENTS.find((a) => a.id === agent)?.description}
          </p>
        )}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">版本列表（按版本号倒序）</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
            </div>
          ) : versions.length === 0 ? (
            <p className="text-sm text-muted-foreground py-8 text-center">
              暂无版本，点右上角「新建版本」开始管理 Prompt
            </p>
          ) : (
            <div className="space-y-3">
              {versions.map((v) => (
                <div
                  key={v.prompt_id}
                  className="rounded border border-border p-3 space-y-2"
                >
                  <div className="flex items-center justify-between flex-wrap gap-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono text-sm">v{v.version}</span>
                      {v.is_active ? (
                        <Badge className="bg-emerald-500/15 text-emerald-300 border-emerald-500/30">
                          当前激活
                        </Badge>
                      ) : null}
                      {v.note && (
                        <span className="text-xs text-muted-foreground truncate max-w-md">
                          {v.note}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      <span>
                        创建: {v.created_at ? v.created_at.slice(0, 19).replace('T', ' ') : '-'}
                      </span>
                      <span>
                        激活: {v.activated_at ? v.activated_at.slice(0, 19).replace('T', ' ') : '-'}
                      </span>
                    </div>
                  </div>
                  <pre className="text-xs bg-muted/40 rounded p-2 max-h-48 overflow-auto whitespace-pre-wrap font-mono">
                    {v.template}
                  </pre>
                  <div className="flex gap-2">
                    {!v.is_active && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => activate(v.version)}
                      >
                        激活此版本
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => openDiff(v.version)}
                    >
                      <GitCompare className="w-3.5 h-3.5 mr-1" />
                      与 active 对比
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* 新建版本对话框 */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-3xl max-h-[80vh] overflow-auto">
          <DialogHeader>
            <DialogTitle>
              新建 Prompt 版本 · {AGENTS.find((a) => a.id === agent)?.label}
            </DialogTitle>
            <DialogDescription>
              创建后会自动激活此版本（旧版本 is_active=false）。注意：processor / reviewer /
              coordinator 的模板含 .format() 占位符，覆盖时需保留。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-muted-foreground">备注（可选）</label>
              <Textarea
                value={newNote}
                onChange={(e) => setNewNote(e.target.value)}
                rows={2}
                placeholder="例如：增加 P0 风险显式提示"
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground">Prompt 模板</label>
              <Textarea
                value={newTemplate}
                onChange={(e) => setNewTemplate(e.target.value)}
                rows={12}
                placeholder="粘贴 Prompt 模板..."
                className="font-mono text-xs"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)} disabled={creating}>
              取消
            </Button>
            <Button onClick={submitCreate} disabled={creating || !newTemplate.trim()}>
              {creating ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : null}
              创建并激活
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Diff 对话框 */}
      <Dialog open={diffOpen} onOpenChange={setDiffOpen}>
        <DialogContent className="max-w-4xl max-h-[80vh] overflow-auto">
          <DialogHeader>
            <DialogTitle>
              版本对比 · v{diffFrom} → v{diffTo}
            </DialogTitle>
            <DialogDescription>
              统一 diff 格式（unified diff）。
            </DialogDescription>
          </DialogHeader>
          {diffLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <pre className="text-xs bg-muted/40 rounded p-3 font-mono whitespace-pre-wrap max-h-[60vh] overflow-auto">
              {diffText}
            </pre>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDiffOpen(false)}>
              关闭
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
