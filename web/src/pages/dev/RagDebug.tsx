import { useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { AlertTriangle, DatabaseZap, Loader2, Search } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import type { RagDebugResult, RagDebugResponse } from '@/types'

const MODES = ['hybrid', 'vector', 'bm25'] as const

export function RagDebug() {
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<(typeof MODES)[number]>('hybrid')
  const [topK, setTopK] = useState('5')
  const [rerankTopK, setRerankTopK] = useState('3')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<RagDebugResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function runDebug() {
    const trimmed = query.trim()
    if (!trimmed || loading) return
    setLoading(true)
    setError(null)
    try {
      const data = await api.debugRag({
        query: trimmed,
        mode,
        top_k: clampNumber(topK, 1, 20, 5),
        rerank_top_k: clampNumber(rerankTopK, 0, 20, 3),
      })
      setResult(data)
    } catch (e) {
      if (e instanceof ApiError) {
        const body = e.body as { error?: string; detail?: string } | null
        setError(body?.detail || body?.error || e.detail || e.message)
      } else {
        setError(e instanceof Error ? e.message : '调试失败')
      }
      setResult(null)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-4 p-4 md:p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <DatabaseZap className="h-5 w-5 text-primary" />
            RAG 检索调试
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            查看 rag-service 召回、重排、分数和异常状态
          </p>
        </div>
        <Badge variant={error ? 'destructive' : result ? 'default' : 'outline'}>
          {loading ? '调试中' : error ? '异常' : result ? '已返回' : '待查询'}
        </Badge>
      </header>

      <Card>
        <CardContent className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_140px_120px_120px_auto]">
          <Textarea
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="输入工单问题或检索关键词"
            rows={3}
            className="lg:col-span-1"
          />
          <div className="space-y-1.5">
            <label className="text-xs text-muted-foreground">模式</label>
            <Select value={mode} onValueChange={(value) => setMode(value as typeof mode)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MODES.map((item) => (
                  <SelectItem key={item} value={item}>
                    {item}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <NumberField label="召回数" value={topK} onChange={setTopK} />
          <NumberField label="重排数" value={rerankTopK} onChange={setRerankTopK} />
          <div className="flex items-end">
            <Button onClick={runDebug} disabled={loading || !query.trim()} className="w-full">
              {loading ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Search className="mr-2 h-4 w-4" />
              )}
              查询
            </Button>
          </div>
        </CardContent>
      </Card>

      {error && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="flex items-center gap-2 text-sm text-destructive">
            <AlertTriangle className="h-4 w-4" />
            {error}
          </CardContent>
        </Card>
      )}

      {result && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <ResultList
            title={`召回结果 · ${result.retrieval.hit_count}`}
            items={result.retrieval.results}
          />
          <ResultList
            title={`重排结果 · ${result.rerank.hit_count}`}
            items={result.rerank.results}
          />
        </div>
      )}
    </div>
  )
}

function NumberField({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs text-muted-foreground">{label}</label>
      <Input
        type="number"
        min={0}
        max={20}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  )
}

function ResultList({ title, items }: { title: string; items: RagDebugResult[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {items.length === 0 ? (
          <div className="py-8 text-center text-sm text-muted-foreground">无结果</div>
        ) : (
          items.map((item, index) => (
            <div key={`${item.id ?? 'chunk'}-${index}`} className="rounded-md border border-border p-3">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">
                    {String(item.metadata.title ?? item.id ?? `chunk-${index + 1}`)}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    chunk {item.chunk_index}
                  </p>
                </div>
                <Badge variant="outline" className="font-mono">
                  {item.score.toFixed(3)}
                </Badge>
              </div>
              <p className="mt-2 line-clamp-4 text-sm text-muted-foreground">
                {item.content}
              </p>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  )
}

function clampNumber(raw: string, min: number, max: number, fallback: number): number {
  const parsed = Number(raw)
  if (!Number.isFinite(parsed)) return fallback
  return Math.min(max, Math.max(min, Math.trunc(parsed)))
}
