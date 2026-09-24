import type { Message, OllamaModel } from '@/types'
import type { Tile, Decision, DocConversationSummary } from '@/lib/docAgent'

export interface KNode {
  id: string
  kind: 'document' | 'question' | 'decision' | 'model' | 'answer'
  label: string
  data: Record<string, unknown>
  conversationId: string
  runIds: string[]
  messageIds: string[]
  highlighted?: boolean
}

export interface KEdge {
  id: string
  source: string
  target: string
  label: string
  kind: string
  failed?: boolean
  highlighted?: boolean
}

export interface KGraph {
  nodes: KNode[]
  edges: KEdge[]
}

export interface LayoutNode extends KNode {
  position: { x: number; y: number }
}

export interface LayoutEdge extends KEdge {}

interface LayoutOptions {
  direction?: 'LR' | 'TB'
  columnGapX?: number
  rowGapY?: number
  decisionRowGap?: number
}

// Node size constants (in pixels) for each kind
export const NODE_SIZES = {
  document: { width: 180, height: 56 },
  question: { width: 200, height: 64 },
  decision: { width: 190, height: 52 },
  model: { width: 190, height: 64 },
}

const MIN_NODE_SPACING = 18 // pixels between node edges

/**
 * Build a knowledge graph from document agent conversations.
 * Robust to missing/partial data; never throws on incomplete tiles or metadata.
 */
export function buildKnowledgeGraph(
  convs: { id: string; title: string; messages: Message[] }[],
  opts?: { highlightMessageId?: string | null },
): KGraph {
  const nodes: KNode[] = []
  const edges: KEdge[] = []
  const modelNodeMap = new Map<string, KNode>()
  const docNodesMap = new Map<string, KNode>()
  const questionNodesMap = new Map<string, KNode>()
  const decisionNodesMap = new Map<string, KNode>()

  const highlightMessageId = opts?.highlightMessageId || null
  let highlightPath = new Set<string>()

  // Pass 1: collect all data
  for (const conv of convs) {
    const convTitle = conv.title || `Conversation ${conv.id}`
    let lastAssistantMessageId = ''
    let currentRunId = ''

    for (let i = 0; i < conv.messages.length; i++) {
      const msg = conv.messages[i]

      // User message with doc_user meta
      if (msg.role === 'user' && msg.meta?.kind === 'doc_user') {
        const userMeta = msg.meta as any
        const attachments = userMeta.attachments || []
        const carriedOver = userMeta.carried_over ?? false

        // Create question node
        const questionText = msg.content || '(no instruction)'
        const questionPreview = questionText.length > 60 ? questionText.substring(0, 60) + '…' : questionText
        const questionNodeId = `question-${msg.id}`

        const questionNode: KNode = {
          id: questionNodeId,
          kind: 'question',
          label: questionPreview,
          data: {
            fullText: questionText,
            timestamp: msg.timestamp,
            conversationTitle: convTitle,
            conversationId: conv.id,
          },
          conversationId: conv.id,
          runIds: [],
          messageIds: [msg.id],
        }
        nodes.push(questionNode)
        questionNodesMap.set(msg.id, questionNode)

        // Create document nodes and edges
        for (const att of attachments) {
          const docId = att.doc_id
          const docNodeId = `document-${docId}`

          let docNode = docNodesMap.get(docId)
          if (!docNode) {
            docNode = {
              id: docNodeId,
              kind: 'document',
              label: att.name || `Doc ${docId}`,
              data: {
                docId,
                filename: att.name || `Document ${docId}`,
                kind: inferDocKind(att.name || ''),
                pages: att.pages ?? null,
                roles: [],
              },
              conversationId: conv.id,
              runIds: [],
              messageIds: [],
            }
            nodes.push(docNode)
            docNodesMap.set(docId, docNode)
          }

          // Track this document's appearance in conversations
          if (!docNode.conversationId || docNode.conversationId === conv.id) {
            if (!docNode.messageIds.includes(msg.id)) {
              docNode.messageIds.push(msg.id)
            }
          }

          // Create document→question edge
          const edgeKind = 'about'
          const roleLabel = att.role && att.role !== 'auto' ? ` · ${att.role}` : ''
          const carriedLabel = carriedOver ? ' (carried over)' : ''
          const edgeLabel = `about${roleLabel}${carriedLabel}`

          edges.push({
            id: `edge-${docId}-${msg.id}`,
            source: docNodeId,
            target: questionNodeId,
            label: edgeLabel,
            kind: edgeKind,
          })

          // Track role
          if (att.role && att.role !== 'auto') {
            const roles = (docNode.data.roles as string[]) || []
            if (!roles.includes(att.role)) {
              roles.push(att.role)
              docNode.data.roles = roles
            }
          }
        }
      }

      // Assistant message with doc_run meta
      if (msg.role === 'assistant' && msg.meta?.kind === 'doc_run') {
        const runMeta = msg.meta as any
        const runId = runMeta.run_id
        const intent = runMeta.intent
        const docIds = runMeta.doc_ids || []
        const tiles = (runMeta.tiles || []) as Tile[]
        const error = runMeta.error
        const cancelled = runMeta.cancelled
        const stats = runMeta.stats

        currentRunId = runId
        lastAssistantMessageId = msg.id

        // Find the question that triggered this run (previous user message)
        let linkedQuestionNodeId: string | null = null
        for (let j = i - 1; j >= 0; j--) {
          if (conv.messages[j].role === 'user' && conv.messages[j].meta?.kind === 'doc_user') {
            linkedQuestionNodeId = `question-${conv.messages[j].id}`
            const qNode = questionNodesMap.get(conv.messages[j].id)
            if (qNode) {
              if (!qNode.runIds.includes(runId)) {
                qNode.runIds.push(runId)
              }
              if (!qNode.messageIds.includes(msg.id)) {
                qNode.messageIds.push(msg.id)
              }
              // Store answer info on question node
              const answerStatus = error ? 'error' : cancelled ? 'cancelled' : 'done'
              const answerText = msg.content || ''
              const answerPreview = answerText.length > 300 ? answerText.substring(0, 300) + '…' : answerText
              qNode.data.answer = {
                model: msg.model,
                ms: stats?.total_ms ?? null,
                status: answerStatus,
                error,
                cancelled,
                preview: answerPreview,
              }
            }
            break
          }
        }

        // Process tiles to extract decisions and models
        const tilesByKind: Record<string, Tile[]> = {}
        for (const tile of tiles) {
          if (!tilesByKind[tile.kind]) {
            tilesByKind[tile.kind] = []
          }
          tilesByKind[tile.kind].push(tile)

          // Handle extract/ocr tiles: add edge to document with OCR status
          if ((tile.kind === 'extract' || tile.kind === 'ocr') && tile.doc) {
            const docId = tile.doc.doc_id
            const docNodeId = `document-${docId}`
            if (linkedQuestionNodeId && tile.status === 'done' && tile.kind === 'extract' && tile.detail?.includes('skipped')) {
              // Text layer present, skip OCR
              const edgeId = `edge-extract-${msg.id}-${docId}`
              const existing = edges.find(e => e.id === edgeId)
              if (!existing) {
                edges.push({
                  id: edgeId,
                  source: docNodeId,
                  target: linkedQuestionNodeId,
                  label: 'OCR skipped (text layer)',
                  kind: 'extract-text',
                })
              }
            }
          }
        }

        // Process Laya tile for decisions
        const layaTile = tiles.find(t => t.kind === 'laya')
        if (layaTile) {
          for (const decision of layaTile.decisions || []) {
            const decisionNodeId = `decision-${runId}-${decision.id}`

            // Format label based on decision type
            let displayLabel = decision.label
            if (decision.id === 'intent') {
              displayLabel = `intent: ${decision.value}`
            } else if (decision.id.startsWith('role-')) {
              displayLabel = `role: ${decision.value}`
            } else if (decision.id.startsWith('ocr_needed-')) {
              displayLabel = 'OCR needed'
            } else if (decision.id === 'answer_model') {
              displayLabel = `answer model: ${decision.value}`
            } else if (decision.id.startsWith('vision_fallback_')) {
              displayLabel = `fallback: ${decision.value}`
            }

            const decisionNode: KNode = {
              id: decisionNodeId,
              kind: 'decision',
              label: displayLabel,
              data: {
                decisionId: decision.id,
                value: decision.value,
                source: decision.source,
                confidence: decision.confidence,
                note: decision.note,
                probabilities: decision.probabilities,
              },
              conversationId: conv.id,
              runIds: [runId],
              messageIds: [msg.id],
            }
            nodes.push(decisionNode)
            decisionNodesMap.set(decisionNodeId, decisionNode)

            // Edge: question → decision
            if (linkedQuestionNodeId) {
              const confidenceStr = decision.confidence !== null && decision.confidence !== undefined
                ? ` ${Math.round(decision.confidence * 100)}%`
                : ''
              const sourceLabel = {
                laya: `decided by Laya${confidenceStr}`,
                rules: 'keyword rules',
                probe: 'probe',
                config: 'config',
                user: 'you',
              }[decision.source] || decision.source

              edges.push({
                id: `edge-question-decision-${decisionNodeId}`,
                source: linkedQuestionNodeId,
                target: decisionNodeId,
                label: sourceLabel,
                kind: 'decide',
              })
            }

            // Edge: decision → model (for answer_model and vision fallbacks)
            if (decision.id === 'answer_model' && decision.value) {
              const modelNodeId = `model-${decision.value}`
              let modelNode = modelNodeMap.get(decision.value)
              if (!modelNode) {
                modelNode = {
                  id: modelNodeId,
                  kind: 'model',
                  label: decision.value,
                  data: {
                    modelName: decision.value,
                    roles: [],
                    useCount: 0,
                  },
                  conversationId: conv.id,
                  runIds: [runId],
                  messageIds: [msg.id],
                }
                nodes.push(modelNode)
                modelNodeMap.set(decision.value, modelNode)
              }

              const edgeLabel = decision.note || 'answer model'
              edges.push({
                id: `edge-decision-model-${decisionNodeId}`,
                source: decisionNodeId,
                target: modelNodeId,
                label: edgeLabel,
                kind: 'answer-model',
              })

              // Track role
              const roles = (modelNode.data.roles as string[]) || []
              if (!roles.includes('answer')) {
                roles.push('answer')
                modelNode.data.roles = roles
              }
              modelNode.data.useCount = ((modelNode.data.useCount as number) || 0) + 1
            } else if (decision.id.startsWith('vision_fallback_')) {
              // Fallback decision: edge to next model or mark failure
              if (decision.value) {
                const modelNodeId = `model-${decision.value}`
                let modelNode = modelNodeMap.get(decision.value)
                if (!modelNode) {
                  modelNode = {
                    id: modelNodeId,
                    kind: 'model',
                    label: decision.value,
                    data: {
                      modelName: decision.value,
                      roles: [],
                      useCount: 0,
                    },
                    conversationId: conv.id,
                    runIds: [runId],
                    messageIds: [msg.id],
                  }
                  nodes.push(modelNode)
                  modelNodeMap.set(decision.value, modelNode)
                }

                edges.push({
                  id: `edge-fallback-${decisionNodeId}`,
                  source: decisionNodeId,
                  target: modelNodeId,
                  label: 'fallback',
                  kind: 'vision-fallback',
                })
              }
            }
          }

          // Laya model node
          const layaNodeId = 'model-Laya'
          let layaNode = modelNodeMap.get('Laya')
          if (!layaNode) {
            layaNode = {
              id: layaNodeId,
              kind: 'model',
              label: 'Laya (decision model)',
              data: {
                modelName: 'Laya',
                roles: ['decision'],
                useCount: 0,
              },
              conversationId: conv.id,
              runIds: [runId],
              messageIds: [msg.id],
            }
            nodes.push(layaNode)
            modelNodeMap.set('Laya', layaNode)
          }

          // Edge: intent decision → Laya
          const intentDecision = layaTile.decisions?.find(d => d.id === 'intent')
          if (intentDecision) {
            const intDecNodeId = `decision-${runId}-intent`
            edges.push({
              id: `edge-intent-laya-${runId}`,
              source: intDecNodeId,
              target: layaNodeId,
              label: 'classified by',
              kind: 'laya-classify',
            })
          }
        }

        // Track any models from non-Laya tiles (ocr, vision, etc.)
        for (const tile of tiles) {
          if (tile.model && tile.kind !== 'laya' && !tile.kind.startsWith('llm')) {
            const modelNodeId = `model-${tile.model}`
            let modelNode = modelNodeMap.get(tile.model)
            if (!modelNode) {
              modelNode = {
                id: modelNodeId,
                kind: 'model',
                label: tile.model,
                data: {
                  modelName: tile.model,
                  roles: [],
                  useCount: 0,
                },
                conversationId: conv.id,
                runIds: [runId],
                messageIds: [msg.id],
              }
              nodes.push(modelNode)
              modelNodeMap.set(tile.model, modelNode)
            }

            // Determine role from tile kind
            const roleMap: Record<string, string> = {
              ocr: 'ocr',
              extract: 'extract',
              vision: 'vision',
            }
            const role = roleMap[tile.kind] || tile.kind
            const roles = (modelNode.data.roles as string[]) || []
            if (!roles.includes(role)) {
              roles.push(role)
              modelNode.data.roles = roles
            }
            modelNode.data.useCount = ((modelNode.data.useCount as number) || 0) + 1

            // Edge: OCR decision → OCR model
            if (tile.kind === 'ocr') {
              const ocrDecision = tiles
                .find(t => t.kind === 'laya')
                ?.decisions?.find(d => d.id.startsWith('ocr_needed-'))
              if (ocrDecision) {
                const ocrDecNodeId = `decision-${runId}-${ocrDecision.id}`
                const note = ocrDecision.note || 'OCR'
                edges.push({
                  id: `edge-ocr-decision-${tile.id}`,
                  source: ocrDecNodeId,
                  target: modelNodeId,
                  label: `OCR: ${note}`,
                  kind: 'ocr-model',
                })
              }
            }
          }
        }
      }
    }
  }

  // Pass 2: highlight path if requested
  if (highlightMessageId) {
    const highlightNode = nodes.find(n => n.messageIds?.includes(highlightMessageId))
    if (highlightNode) {
      highlightPath = new Set([highlightNode.id])

      // BFS to find all connected nodes
      const visited = new Set<string>()
      const queue = [highlightNode.id]

      while (queue.length > 0) {
        const nodeId = queue.shift()!
        if (visited.has(nodeId)) continue
        visited.add(nodeId)
        highlightPath.add(nodeId)

        // Find all connected edges
        const connectedEdges = edges.filter(e => e.source === nodeId || e.target === nodeId)
        for (const edge of connectedEdges) {
          highlightPath.add(edge.id)
          const nextId = edge.source === nodeId ? edge.target : edge.source
          if (!visited.has(nextId)) {
            queue.push(nextId)
          }
        }
      }
    }
  }

  // Apply highlighting
  for (const node of nodes) {
    if (highlightPath.has(node.id)) {
      node.highlighted = true
    }
  }
  for (const edge of edges) {
    if (highlightPath.has(edge.id)) {
      edge.highlighted = true
    }
  }

  return { nodes, edges }
}

/**
 * Layout nodes intelligently based on direction.
 * LR: left→right columns (document, question, decision, model)
 * TB: top→bottom rows with wrapping (documents row, questions row, decisions row, models row)
 */
export function layoutKnowledgeGraph(
  graph: KGraph,
  opts?: LayoutOptions,
): { nodes: LayoutNode[]; edges: LayoutEdge[] } {
  const direction = opts?.direction ?? 'LR'

  if (direction === 'TB') {
    return layoutKnowledgeGraphTopBottom(graph, opts)
  } else {
    return layoutKnowledgeGraphLeftRight(graph, opts)
  }
}

/**
 * Left→Right layout: 4 vertical columns
 */
function layoutKnowledgeGraphLeftRight(
  graph: KGraph,
  opts?: LayoutOptions,
): { nodes: LayoutNode[]; edges: LayoutEdge[] } {
  const columnGapX = opts?.columnGapX ?? 260
  const rowGapY = opts?.rowGapY ?? 70
  const decisionRowGap = opts?.decisionRowGap ?? 48

  const columns: Record<string, KNode[]> = {
    document: [],
    question: [],
    decision: [],
    model: [],
  }

  // Group nodes by column
  for (const node of graph.nodes) {
    if (node.kind === 'document') columns.document.push(node)
    else if (node.kind === 'question') columns.question.push(node)
    else if (node.kind === 'decision') columns.decision.push(node)
    else if (node.kind === 'model') columns.model.push(node)
  }

  // Sort within columns
  columns.question.sort((a, b) => {
    const aTime = (a.data.timestamp as number) || 0
    const bTime = (b.data.timestamp as number) || 0
    return aTime - bTime
  })

  const questionToFirstEdge: Record<string, number> = {}
  graph.edges.forEach((e, i) => {
    if (e.kind === 'about' && !questionToFirstEdge[e.target]) {
      questionToFirstEdge[e.target] = i
    }
  })
  columns.document.sort((a, b) => {
    const aEdges = graph.edges.filter(e => e.kind === 'about' && e.source === a.id)
    const bEdges = graph.edges.filter(e => e.kind === 'about' && e.source === b.id)
    const aMin = Math.min(...aEdges.map(e => questionToFirstEdge[e.target] ?? 999))
    const bMin = Math.min(...bEdges.map(e => questionToFirstEdge[e.target] ?? 999))
    return aMin - bMin
  })

  columns.decision.sort((a, b) => {
    const aQuestionEdges = graph.edges.filter(e => e.kind === 'decide' && e.target === a.id)
    const bQuestionEdges = graph.edges.filter(e => e.kind === 'decide' && e.target === b.id)
    const aQuestionIdx = aQuestionEdges[0] ? columns.question.findIndex(q => q.id === aQuestionEdges[0].source) : 999
    const bQuestionIdx = bQuestionEdges[0] ? columns.question.findIndex(q => q.id === bQuestionEdges[0].source) : 999
    if (aQuestionIdx !== bQuestionIdx) return aQuestionIdx - bQuestionIdx
    return (a.data.decisionId as string || '').localeCompare(b.data.decisionId as string || '')
  })

  columns.model.sort((a, b) => {
    const aEdges = graph.edges.filter(e => e.target === a.id)
    const bEdges = graph.edges.filter(e => e.target === b.id)
    const aIdx = aEdges.length > 0 ? graph.edges.findIndex(e => e.target === a.id) : 999
    const bIdx = bEdges.length > 0 ? graph.edges.findIndex(e => e.target === b.id) : 999
    return aIdx - bIdx
  })

  const positionedNodes: LayoutNode[] = []
  const columnX: Record<string, number> = {
    document: 0,
    question: columnGapX,
    decision: columnGapX * 2,
    model: columnGapX * 3,
  }

  let columnY: Record<string, number> = {
    document: 0,
    question: 0,
    decision: 0,
    model: 0,
  }

  for (const [kind, nodes] of Object.entries(columns)) {
    columnY[kind] = 0
    for (const node of nodes) {
      const x = columnX[kind]
      const y = columnY[kind]

      positionedNodes.push({
        ...node,
        position: { x, y },
      })

      if (kind === 'decision') {
        columnY[kind] += NODE_SIZES.decision.height + MIN_NODE_SPACING
      } else {
        const nodeSize = NODE_SIZES[kind as keyof typeof NODE_SIZES] || NODE_SIZES.document
        columnY[kind] += nodeSize.height + MIN_NODE_SPACING
      }
    }
  }

  return {
    nodes: positionedNodes,
    edges: graph.edges,
  }
}

/**
 * Top→Bottom layout: 4 horizontal rows, nodes wrap within each row to keep width ~300px per layer
 * Rows are centered horizontally and layers are properly separated
 */
function layoutKnowledgeGraphTopBottom(
  graph: KGraph,
  opts?: LayoutOptions,
): { nodes: LayoutNode[]; edges: LayoutEdge[] } {
  const maxRowWidth = opts?.columnGapX ?? 300 // wrap nodes if wider
  const rowGapY = opts?.rowGapY ?? 70
  const horizontalGap = 18

  const layers: Record<string, KNode[]> = {
    document: [],
    question: [],
    decision: [],
    model: [],
  }

  // Group nodes by layer
  for (const node of graph.nodes) {
    if (node.kind === 'document') layers.document.push(node)
    else if (node.kind === 'question') layers.question.push(node)
    else if (node.kind === 'decision') layers.decision.push(node)
    else if (node.kind === 'model') layers.model.push(node)
  }

  // Sort within layers
  layers.question.sort((a, b) => {
    const aTime = (a.data.timestamp as number) || 0
    const bTime = (b.data.timestamp as number) || 0
    return aTime - bTime
  })

  const questionToFirstEdge: Record<string, number> = {}
  graph.edges.forEach((e, i) => {
    if (e.kind === 'about' && !questionToFirstEdge[e.target]) {
      questionToFirstEdge[e.target] = i
    }
  })
  layers.document.sort((a, b) => {
    const aEdges = graph.edges.filter(e => e.kind === 'about' && e.source === a.id)
    const bEdges = graph.edges.filter(e => e.kind === 'about' && e.source === b.id)
    const aMin = Math.min(...aEdges.map(e => questionToFirstEdge[e.target] ?? 999))
    const bMin = Math.min(...bEdges.map(e => questionToFirstEdge[e.target] ?? 999))
    return aMin - bMin
  })

  layers.decision.sort((a, b) => {
    const aQuestionEdges = graph.edges.filter(e => e.kind === 'decide' && e.target === a.id)
    const bQuestionEdges = graph.edges.filter(e => e.kind === 'decide' && e.target === b.id)
    const aQuestionIdx = aQuestionEdges[0] ? layers.question.findIndex(q => q.id === aQuestionEdges[0].source) : 999
    const bQuestionIdx = bQuestionEdges[0] ? layers.question.findIndex(q => q.id === bQuestionEdges[0].source) : 999
    if (aQuestionIdx !== bQuestionIdx) return aQuestionIdx - bQuestionIdx
    return (a.data.decisionId as string || '').localeCompare(b.data.decisionId as string || '')
  })

  layers.model.sort((a, b) => {
    const aEdges = graph.edges.filter(e => e.target === a.id)
    const bEdges = graph.edges.filter(e => e.target === b.id)
    const aIdx = aEdges.length > 0 ? graph.edges.findIndex(e => e.target === a.id) : 999
    const bIdx = bEdges.length > 0 ? graph.edges.findIndex(e => e.target === b.id) : 999
    return aIdx - bIdx
  })

  const positionedNodes: LayoutNode[] = []
  let layerY = 0

  // Process each layer
  for (const [layerKind, layerNodes] of Object.entries(layers)) {
    if (layerNodes.length === 0) continue

    const nodeSize = NODE_SIZES[layerKind as keyof typeof NODE_SIZES] || NODE_SIZES.document

    // Calculate rows for this layer
    interface Row {
      nodes: KNode[]
      width: number
      maxHeight: number
    }
    const rows: Row[] = []
    let currentRow: Row = { nodes: [], width: 0, maxHeight: 0 }

    for (const node of layerNodes) {
      const nodeWidth = nodeSize.width
      const nodeHeight = nodeSize.height

      // Check if node fits in current row
      if (currentRow.nodes.length > 0 && currentRow.width + horizontalGap + nodeWidth > maxRowWidth) {
        // Start new row
        rows.push(currentRow)
        currentRow = { nodes: [], width: 0, maxHeight: 0 }
      }

      currentRow.nodes.push(node)
      currentRow.width += (currentRow.nodes.length === 1 ? 0 : horizontalGap) + nodeWidth
      currentRow.maxHeight = Math.max(currentRow.maxHeight, nodeHeight)
    }
    if (currentRow.nodes.length > 0) {
      rows.push(currentRow)
    }

    // Calculate total layer height first
    let layerHeight = 0
    for (let i = 0; i < rows.length; i++) {
      layerHeight += rows[i].maxHeight
      if (i < rows.length - 1) {
        layerHeight += MIN_NODE_SPACING
      }
    }

    // Position nodes in rows, centered horizontally
    let rowY = layerY
    for (let rowIndex = 0; rowIndex < rows.length; rowIndex++) {
      const row = rows[rowIndex]

      // Center this row horizontally around x=0
      const rowStartX = -row.width / 2
      let nodeX = rowStartX

      for (const node of row.nodes) {
        const nodeWidth = nodeSize.width

        positionedNodes.push({
          ...node,
          position: { x: nodeX, y: rowY },
        })

        nodeX += nodeWidth + horizontalGap
      }

      // Move to next row
      rowY += row.maxHeight
      if (rowIndex < rows.length - 1) {
        rowY += MIN_NODE_SPACING
      }
    }

    // Move to next layer with proper gap
    layerY += layerHeight + rowGapY
  }

  return {
    nodes: positionedNodes,
    edges: graph.edges,
  }
}

function inferDocKind(filename: string): string {
  const lower = filename.toLowerCase()
  if (lower.endsWith('.pdf')) return 'pdf'
  if (/\.(png|jpg|jpeg|gif|webp|svg)$/i.test(lower)) return 'image'
  if (/\.(eml|msg)$/i.test(lower)) return 'email'
  if (/\.(docx?|odt|rtf)$/i.test(lower)) return 'docx'
  if (/\.(xlsx?|csv|ods)$/i.test(lower)) return 'sheet'
  if (/\.(txt|md)$/i.test(lower)) return 'text'
  return 'other'
}
