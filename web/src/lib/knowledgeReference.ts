import type { KnowledgeDocument } from '@/types'

type KnowledgeFilter = {
  query: string
  category?: string
}

export function buildKnowledgeSearchParams(reference: string): URLSearchParams {
  const extracted = parseKnowledgeReference(reference)
  const params = new URLSearchParams()
  params.set('q', extracted.title || extracted.fallback)
  return params
}

export function filterKnowledgeDocuments(
  documents: KnowledgeDocument[],
  filter: KnowledgeFilter,
): KnowledgeDocument[] {
  const keyword = normalizeText(filter.query)
  const category = normalizeText(filter.category || '')
  const tokens = tokenize([keyword, category].filter(Boolean).join(' '))

  return documents.filter((doc) => {
    if (!keyword && !category) return true

    const source = normalizeText(doc.source)
    const docCategory = normalizeText(doc.category)
    const haystack = normalizeText([doc.source, doc.category].filter(Boolean).join(' '))

    if (keyword && haystack.includes(keyword)) return true
    if (keyword && source && keyword.includes(source)) return true
    if (category && docCategory && (category.includes(docCategory) || docCategory.includes(category))) {
      return true
    }
    return tokens.some((token) => haystack.includes(token))
  })
}

function parseKnowledgeReference(reference: string): { title: string; fallback: string } {
  const title = reference.match(/标题:\s*([^；;，,\n]+)/)?.[1]?.trim() || ''
  const fallback = reference
    .replace(/检索到以下知识片段[:：]?/g, '')
    .replace(/^\s*\d+\.\s*/, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 30)
  return { title, fallback }
}

function tokenize(value: string): string[] {
  return normalizeText(value)
    .split(/[\s:：;；,，.。#()[\]（）【】<>《》/\\|、—_>-]+/)
    .filter((item) => item.length >= 2)
}

function normalizeText(value: string): string {
  return value.trim().toLowerCase()
}
