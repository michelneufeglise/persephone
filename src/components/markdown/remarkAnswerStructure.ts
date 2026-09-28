/**
 * remark plugin: light structural post-processing of an answer's mdast so the
 * renderer can style it — without changing any text.
 *
 *  • Callout sections — a heading (or a bold-only "lead" paragraph such as
 *    `**Conclusion**`) whose text reads Conclusion / Conclusie / Summary /
 *    Samenvatting / Verdict is wrapped, together with the content that follows
 *    it up to the next heading of the same or a higher level, in a
 *    `<div class="rmd-callout rmd-callout--conclusion">`. Caveats / Kanttekeningen
 *    / Warning / Note get the amber `rmd-callout--caution` variant.
 *  • Lead paragraphs — a paragraph that is just `**Bold label**` is tagged
 *    `rmd-lead` so it can be spaced like a small heading.
 *  • Report variant — an ornamental break is inserted between H2 sections and
 *    the first paragraph is tagged `rmd-lede`.
 *
 * It only regroups top-level nodes, so it is deterministic on partial
 * markdown while tokens stream in, and any unexpected shape simply falls
 * through untouched (the whole transform is guarded by try/catch).
 */

interface MdNode {
  type: string
  depth?: number
  value?: string
  children?: MdNode[]
  data?: { hName?: string; hProperties?: Record<string, unknown>; [k: string]: unknown }
}

export type AnswerVariant = 'chat' | 'report'
type Tone = 'conclusion' | 'caution'

const CONCLUSION_RE =
  /^(?:(?:final|overall|algemene?)\s+)?(?:eind)?(?:conclusions?|conclusies?|summary|samenvatting|verdict|oordeel)(?![\p{L}\p{N}-])/u
// "Note(s)" only as the whole label ("## Notes", "**Note:**") — not "Notes on
// installation" / "Note-taking apps", which are ordinary section titles.
const CAUTION_RE =
  /^(?:(?:authenticity\b.*\bcaveats?|caveats?|kanttekening(?:en)?|warnings?|waarschuwing(?:en)?|let op)(?![\p{L}\p{N}-])|(?:important\s+)?notes?$)/u

/** Plain text of an mdast node (text + inline code, recursively). */
function textOf(node: MdNode | undefined, depth = 0): string {
  if (!node || depth > 20) return ''
  if (typeof node.value === 'string' && (node.type === 'text' || node.type === 'inlineCode')) return node.value
  if (!Array.isArray(node.children)) return ''
  let s = ''
  for (const c of node.children) s += textOf(c, depth + 1)
  return s
}

/** Lower-case label with leading emoji / numbering / ornaments and a trailing colon removed. */
function normalizeLabel(raw: string): string {
  return raw
    .replace(/^[^\p{L}]+/u, '')
    .replace(/[\s:：.]+$/u, '')
    .replace(/\s+/g, ' ')
    .toLowerCase()
}

function toneFor(label: string): Tone | null {
  if (!label || label.length > 60) return null
  if (CONCLUSION_RE.test(label)) return 'conclusion'
  if (CAUTION_RE.test(label)) return 'caution'
  return null
}

const isBlankText = (n: MdNode) => n.type === 'text' && !(n.value ?? '').replace(/[\s:：]/g, '')

/** `**Label**` alone in a paragraph (optionally followed by a colon / whitespace). */
function isLeadParagraph(node: MdNode): boolean {
  if (node.type !== 'paragraph' || !Array.isArray(node.children) || node.children.length === 0) return false
  const [first, ...rest] = node.children
  if (first.type !== 'strong') return false
  if (!rest.every(isBlankText)) return false
  const t = textOf(first).trim()
  return t.length > 0 && t.length <= 80
}

/** `**Conclusion**` + line break + text in one paragraph (models do this a lot). */
function leadLabelOf(node: MdNode): string | null {
  if (node.type !== 'paragraph' || !Array.isArray(node.children) || node.children.length === 0) return null
  const [first, second] = node.children
  if (first.type !== 'strong') return null
  if (isLeadParagraph(node) || second?.type === 'break') return textOf(first)
  return null
}

const LEAD_LEVEL = 7 // sits below h6 in the hierarchy

function calloutOf(node: MdNode): { tone: Tone; level: number } | null {
  if (node.type === 'heading') {
    const tone = toneFor(normalizeLabel(textOf(node)))
    return tone ? { tone, level: node.depth ?? 6 } : null
  }
  const lead = leadLabelOf(node)
  if (lead != null) {
    const tone = toneFor(normalizeLabel(lead))
    return tone ? { tone, level: LEAD_LEVEL } : null
  }
  return null
}

function endsSection(node: MdNode, level: number): boolean {
  if (node.type === 'heading') return (node.depth ?? 6) <= level
  if (node.type === 'thematicBreak') return true
  if (level === LEAD_LEVEL) return leadLabelOf(node) != null
  return false
}

function addClass(node: MdNode, cls: string) {
  const data = (node.data ??= {})
  const props = (data.hProperties ??= {})
  const prev = props.className
  const list = Array.isArray(prev) ? prev.map(String) : typeof prev === 'string' ? prev.split(/\s+/) : []
  if (!list.includes(cls)) list.push(cls)
  props.className = list
}

function insertReportBreaks(children: MdNode[]): MdNode[] {
  const out: MdNode[] = []
  let seenH2 = false
  for (const node of children) {
    if (node.type === 'heading' && node.depth === 2) {
      const prev = out[out.length - 1]
      if (seenH2 && prev && prev.type !== 'thematicBreak') out.push({ type: 'thematicBreak' })
      seenH2 = true
    }
    out.push(node)
  }
  return out
}

function wrapCallouts(children: MdNode[]): MdNode[] {
  const out: MdNode[] = []
  let i = 0
  while (i < children.length) {
    const node = children[i]
    const hit = calloutOf(node)
    if (!hit) { out.push(node); i++; continue }
    const body: MdNode[] = [node]
    let j = i + 1
    while (j < children.length && !endsSection(children[j], hit.level)) { body.push(children[j]); j++ }
    out.push({
      type: 'rmdCallout',
      children: body,
      data: {
        hName: 'div',
        hProperties: { className: ['rmd-callout', `rmd-callout--${hit.tone}`] },
      },
    })
    i = j
  }
  return out
}

function transform(tree: MdNode, variant: AnswerVariant) {
  if (!tree || !Array.isArray(tree.children)) return
  let children = tree.children

  for (const node of children) {
    if (isLeadParagraph(node)) addClass(node, 'rmd-lead')
  }
  if (variant === 'report') {
    const firstPara = children.find(n => n.type === 'paragraph' && !isLeadParagraph(n))
    const firstH2 = children.findIndex(n => n.type === 'heading' && n.depth === 2)
    if (firstPara && (firstH2 < 0 || children.indexOf(firstPara) < firstH2)) addClass(firstPara, 'rmd-lede')
    children = insertReportBreaks(children)
  }
  tree.children = wrapCallouts(children)
}

/** unified attacher — use as `remarkPlugins={[remarkGfm, [remarkAnswerStructure, { variant }]]}` */
export function remarkAnswerStructure(options?: { variant?: AnswerVariant }) {
  const variant: AnswerVariant = options?.variant === 'report' ? 'report' : 'chat'
  return (tree: unknown) => {
    try {
      transform(tree as MdNode, variant)
    } catch (err) {
      // Never let a styling pass break rendering — fall back to the plain tree.
      // eslint-disable-next-line no-console
      console.warn('[remarkAnswerStructure] skipped:', err)
    }
  }
}

// Exported for unit-style checks.
export const __test = { normalizeLabel, toneFor, isLeadParagraph, calloutOf }
