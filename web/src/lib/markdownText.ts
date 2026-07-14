const CODE_FENCE_PATTERN = /(```[\s\S]*?```)/g
const NUMBERED_SECTION_PATTERN = /^(\d{1,2})\. ([^。\n：:]{2,28}(?:指引|提示|信息|内容|方式|问题|事项|要求|流程|确认|状态|入口|说明|建议|结论|路径|规则|材料))\s+(.+)$/
const LIST_ITEM_WITH_PARAGRAPH_PATTERN = /^- ([^。\n：:]{2,24}(?:页面|门户|助手|系统|平台|入口|流程|工具|客户端|账号|设备))\s+(针对|若|建议|目前|由于|公司|具体|通常|为了)(.+)$/

export function normalizeMarkdownText(value: string) {
  const text = value.replace(/\r\n?/g, '\n')

  return text
    .split(CODE_FENCE_PATTERN)
    .map((segment) => segment.startsWith('```') ? segment : normalizeLooseListMarkers(segment))
    .join('')
    .trim()
}

function normalizeLooseListMarkers(value: string) {
  let normalized = value

  normalized = normalized.replace(/([^\n])\s+(\d{1,2})\.\s+(?=\S)/g, '$1\n\n$2. ')
  normalized = normalized.replace(/(^|[^\n])\s+\*\s+(?=\S)(?!\*)/g, '$1\n\n- ')

  let compacted = normalized
  do {
    normalized = compacted
    compacted = normalized.replace(/(\n- [^\n]+)\n{2,}(?=- )/g, '$1\n')
  } while (compacted !== normalized)

  return normalized
    .split('\n')
    .map(splitListItemParagraph)
    .map(formatNumberedSection)
    .join('\n')
}

function splitListItemParagraph(line: string) {
  const match = line.match(LIST_ITEM_WITH_PARAGRAPH_PATTERN)
  if (!match) return line

  const [, item, starter, rest] = match
  return `- ${item}\n\n${starter}${rest}`
}

function formatNumberedSection(line: string) {
  const match = line.match(NUMBERED_SECTION_PATTERN)
  if (!match) return line

  const [, number, title, content] = match
  return `### ${number}. ${title}\n\n${content}`
}
