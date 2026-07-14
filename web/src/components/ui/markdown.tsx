/**
 * 轻量 Markdown 渲染组件。
 *
 * 用于把 LLM 输出、决策 reason、处理结果等可能含 markdown 的字符串
 * 渲染成富文本（标题、列表、代码块、表格、链接等）。
 *
 * 基于 react-markdown + remark-gfm。
 */

import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { cn } from '@/lib/utils'
import { normalizeMarkdownText } from '@/lib/markdownText'

interface MarkdownProps {
  children: string
  className?: string
}

export function Markdown({ children, className }: MarkdownProps) {
  const normalized = normalizeMarkdownText(children)

  return (
    <div
      className={cn(
        'text-sm leading-relaxed text-foreground/90',
        '[&_p]:my-2 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0',
        '[&_h1]:text-base [&_h1]:font-semibold [&_h1]:mt-3 [&_h1]:mb-1.5',
        '[&_h2]:text-[15px] [&_h2]:font-semibold [&_h2]:mt-3 [&_h2]:mb-1.5',
        '[&_h3]:text-sm [&_h3]:font-semibold [&_h3]:mt-2 [&_h3]:mb-1',
        '[&_ul]:list-disc [&_ul]:pl-5 [&_ul]:my-1.5 [&_ul_li]:my-0.5',
        '[&_ol]:list-decimal [&_ol]:pl-5 [&_ol]:my-1.5 [&_ol_li]:my-0.5',
        '[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-[12px] [&_code]:font-mono',
        '[&_pre]:rounded-md [&_pre]:border [&_pre]:border-border [&_pre]:bg-background [&_pre]:p-3 [&_pre]:overflow-x-auto [&_pre_code]:bg-transparent [&_pre_code]:p-0',
        '[&_blockquote]:border-l-2 [&_blockquote]:border-primary/40 [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground',
        '[&_a]:text-primary [&_a]:underline [&_a]:underline-offset-2',
        '[&_table]:w-full [&_table]:border-collapse [&_table]:my-2 [&_table]:text-xs',
        '[&_th]:border [&_th]:border-border [&_th]:bg-muted [&_th]:px-2 [&_th]:py-1 [&_th]:font-medium',
        '[&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1',
        '[&_hr]:my-3 [&_hr]:border-border',
        '[&_strong]:font-semibold [&_strong]:text-foreground',
        className,
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{normalized}</ReactMarkdown>
    </div>
  )
}
