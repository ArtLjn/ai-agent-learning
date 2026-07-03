import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { AlertCircle, Settings2, Bot, Database, Shield, Server, KeyRound, Cpu } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import type { SystemConfig } from '@/types'

type BadgeState = 'configured' | 'empty' | 'warning'

function statusBadge(state: BadgeState, label?: string) {
  const cls = {
    configured: 'bg-success/15 text-success',
    empty: 'bg-warning/15 text-warning',
    warning: 'bg-destructive/15 text-destructive',
  }[state]
  return (
    <Badge variant="outline" className={`border-0 text-xs ${cls}`}>
      {label ?? (state === 'configured' ? '已配置' : state === 'empty' ? '未配置' : '注意')}
    </Badge>
  )
}

function ConfigRow({
  label,
  desc,
  children,
}: {
  label: string
  desc?: string
  children: React.ReactNode
}) {
  return (
    <>
      <Separator className="bg-border mb-3" />
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm font-medium">{label}</p>
          {desc && <p className="text-[11px] text-muted-foreground">{desc}</p>}
        </div>
        <div className="flex items-center gap-2 shrink-0">{children}</div>
      </div>
    </>
  )
}

function ValueCode({ value }: { value: string | number | null | undefined }) {
  if (value === null || value === undefined || value === '') {
    return <span className="text-xs text-muted-foreground">-</span>
  }
  return (
    <code className="text-xs font-mono bg-background px-2 py-1 rounded border border-border text-foreground break-all">
      {String(value)}
    </code>
  )
}

export function Settings() {
  const [data, setData] = useState<SystemConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true)
    api
      .getSystemConfig()
      .then((cfg) => {
        if (alive) {
          setData(cfg)
          setError(null)
        }
      })
      .catch((err: unknown) => {
        if (!alive) return
        if (err instanceof ApiError) {
          setError(err.detail || `加载失败 (${err.status})`)
        } else {
          setError('加载系统配置失败')
        }
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [])

  if (loading) return <SettingsSkeleton />
  if (error || !data) {
    return (
      <div className="space-y-4">
        <h2 className="text-xl font-semibold flex items-center gap-2">
          <Settings2 className="w-5 h-5 text-primary" />
          系统配置
        </h2>
        <Card className="bg-card border-destructive/40">
          <CardContent className="pt-6 flex items-center gap-3 text-sm text-destructive">
            <AlertCircle className="w-4 h-4" />
            <span>{error || '加载失败'}</span>
          </CardContent>
        </Card>
      </div>
    )
  }

  // 未配置 session_secret 时高亮警告（生产环境必须改）
  const sessionSecretWarning = !data.auth.session_secret_configured

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold flex items-center gap-2">
          <Settings2 className="w-5 h-5 text-primary" />
          系统配置
        </h2>
        <p className="text-sm text-muted-foreground mt-1">
          {data._meta.note}
        </p>
      </div>

      {/* 只读提示条 */}
      <div className="bg-destructive/10 border border-destructive/30 rounded-md p-3 flex items-center gap-2 text-sm text-destructive">
        <AlertCircle className="w-4 h-4 shrink-0" />
        <span>只读视图，配置修改请联系开发人员通过环境变量调整</span>
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* LLM */}
        <Card className="bg-card border-border">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <Bot className="w-4 h-4 text-primary" />
              LLM 大模型
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <p className="text-sm font-medium">Base URL</p>
                <p className="text-[11px] text-muted-foreground">OpenAI 兼容 API 地址</p>
              </div>
              <ValueCode value={data.llm.base_url} />
            </div>
            <ConfigRow label="API Key" desc="已脱敏，仅展示状态">
              {statusBadge(data.llm.api_key_configured ? 'configured' : 'empty')}
            </ConfigRow>
            <ConfigRow label="默认模型" desc="主处理模型">
              <ValueCode value={data.llm.model} />
            </ConfigRow>
            <ConfigRow label="采样温度" desc="LLM temperature">
              <ValueCode value={data.llm.temperature} />
            </ConfigRow>
            <ConfigRow label="降级模型" desc="模型不可用时切换">
              <ValueCode value={data.llm.fallback_model} />
            </ConfigRow>
            <ConfigRow label="模型路由" desc="按任务分派模型">
              <div className="text-right text-[11px] text-muted-foreground max-w-[180px]">
                {Object.entries(data.llm.model_routes).map(([k, v]) => (
                  <div key={k}>
                    <span className="font-mono">{k}</span> → <span className="font-mono">{v}</span>
                  </div>
                ))}
              </div>
            </ConfigRow>
          </CardContent>
        </Card>

        {/* Embedding */}
        <Card className="bg-card border-border">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <Cpu className="w-4 h-4 text-primary" />
              Embedding 向量嵌入
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <p className="text-sm font-medium">Base URL</p>
                <p className="text-[11px] text-muted-foreground">向量嵌入服务地址</p>
              </div>
              <ValueCode value={data.embedding.base_url} />
            </div>
            <ConfigRow label="API Key" desc="已脱敏">
              {statusBadge(data.embedding.api_key_configured ? 'configured' : 'empty')}
            </ConfigRow>
            <ConfigRow label="模型" desc="嵌入模型">
              <ValueCode value={data.embedding.model} />
            </ConfigRow>
            <ConfigRow label="向量维度" desc="embedding_dim">
              <ValueCode value={data.embedding.dim} />
            </ConfigRow>
          </CardContent>
        </Card>

        {/* Qdrant */}
        <Card className="bg-card border-border">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <Database className="w-4 h-4 text-primary" />
              Qdrant 向量库
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <p className="text-sm font-medium">URL</p>
                <p className="text-[11px] text-muted-foreground">向量数据库地址</p>
              </div>
              <ValueCode value={data.qdrant.url} />
            </div>
            <ConfigRow label="API Key" desc="已脱敏">
              {statusBadge(data.qdrant.api_key_configured ? 'configured' : 'empty')}
            </ConfigRow>
            <ConfigRow label="集合名" desc="知识库 collection">
              <ValueCode value={data.qdrant.collection} />
            </ConfigRow>
            <ConfigRow label="top_k" desc="检索返回数">
              <ValueCode value={data.qdrant.top_k} />
            </ConfigRow>
            <ConfigRow label="score_threshold" desc="检索分数阈值">
              <ValueCode value={data.qdrant.score_threshold} />
            </ConfigRow>
            <ConfigRow label="batch_size" desc="upsert 批量">
              <ValueCode value={data.qdrant.batch_size} />
            </ConfigRow>
          </CardContent>
        </Card>

        {/* RAG Service */}
        <Card className="bg-card border-border">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <Server className="w-4 h-4 text-primary" />
              RAG 服务
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <p className="text-sm font-medium">Base URL</p>
                <p className="text-[11px] text-muted-foreground">独立 rag-service 地址</p>
              </div>
              <ValueCode value={data.rag_service.base_url} />
            </div>
            <ConfigRow label="接入状态" desc="v2.0 主系统作为客户端">
              {data.rag_service.status === 'not_configured'
                ? statusBadge('empty', '未接入')
                : statusBadge('configured', data.rag_service.status)}
            </ConfigRow>
            <ConfigRow label="API Key" desc="已脱敏">
              {statusBadge(data.rag_service.api_key_configured ? 'configured' : 'empty')}
            </ConfigRow>
          </CardContent>
        </Card>

        {/* Database */}
        <Card className="bg-card border-border">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <Database className="w-4 h-4 text-primary" />
              数据库
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <p className="text-sm font-medium">Driver</p>
                <p className="text-[11px] text-muted-foreground">SQLAlchemy 驱动</p>
              </div>
              <ValueCode value={data.database.driver} />
            </div>
            <ConfigRow label="主机" desc="DB host">
              <ValueCode value={data.database.host} />
            </ConfigRow>
            <ConfigRow label="端口" desc="DB port">
              <ValueCode value={data.database.port} />
            </ConfigRow>
            <ConfigRow label="数据库" desc="database name">
              <ValueCode value={data.database.database} />
            </ConfigRow>
            <ConfigRow label="用户名" desc="已脱敏">
              {statusBadge(data.database.username_configured ? 'configured' : 'empty')}
            </ConfigRow>
            <ConfigRow label="密码" desc="已脱敏">
              {statusBadge(data.database.password_configured ? 'configured' : 'empty')}
            </ConfigRow>
          </CardContent>
        </Card>

        {/* Auth */}
        <Card className="bg-card border-border">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2">
              <Shield className="w-4 h-4 text-primary" />
              鉴权与会话
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center justify-between gap-4">
              <div className="min-w-0">
                <p className="text-sm font-medium">鉴权开关</p>
                <p className="text-[11px] text-muted-foreground">auth_enabled</p>
              </div>
              {data.auth.auth_enabled
                ? statusBadge('configured', '已启用')
                : statusBadge('warning', '已关闭')}
            </div>
            <ConfigRow label="管理员密码哈希" desc="已脱敏">
              {statusBadge(data.auth.password_hash_configured ? 'configured' : 'empty')}
            </ConfigRow>
            <ConfigRow label="Session 签名密钥" desc="生产环境必须改默认值">
              {sessionSecretWarning
                ? statusBadge('warning', '仍为默认值')
                : statusBadge('configured')}
            </ConfigRow>
            <ConfigRow label="Session Cookie" desc="cookie 名">
              <ValueCode value={data.auth.session_cookie} />
            </ConfigRow>
            <ConfigRow label="Session 有效期" desc="max_age">
              <ValueCode value={`${data.auth.session_max_age_days} 天`} />
            </ConfigRow>
          </CardContent>
        </Card>
      </div>

      {/* 系统信息 */}
      <Card className="bg-card border-border">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <KeyRound className="w-4 h-4 text-primary" />
            系统信息
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-4 gap-4 text-center">
            <div className="bg-background rounded-md p-4 border border-border">
              <p className="text-lg font-bold text-primary">{data._meta.version}</p>
              <p className="text-[11px] text-muted-foreground">系统版本</p>
            </div>
            <div className="bg-background rounded-md p-4 border border-border">
              <p className="text-lg font-bold text-success">4</p>
              <p className="text-[11px] text-muted-foreground">Agent 数量</p>
            </div>
            <div className="bg-background rounded-md p-4 border border-border">
              <p className="text-lg font-bold text-warning">5+</p>
              <p className="text-[11px] text-muted-foreground">LangGraph 节点</p>
            </div>
            <div className="bg-background rounded-md p-4 border border-border">
              <p className="text-lg font-bold text-primary">MySQL</p>
              <p className="text-[11px] text-muted-foreground">数据存储</p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function SettingsSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-8 w-44" />
      <Skeleton className="h-12 rounded-lg" />
      <div className="grid grid-cols-2 gap-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-56 rounded-lg" />
        ))}
      </div>
      <Skeleton className="h-32 rounded-lg" />
    </div>
  )
}
