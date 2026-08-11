import type { Node, Edge } from '@xyflow/react'

export type FlowNodeType = 'input' | 'llm' | 'research' | 'docop' | 'tool' | 'code' | 'output'

export interface InputConfig {
  kind: 'text' | 'document'
  text?: string
  documentId?: string
}

export interface LlmConfig {
  model: string
  prompt: string
}

export interface ResearchConfig {
  // No additional config; uses the input as the query
}

export interface DocOpConfig {
  operation: 'ocr' | 'summarize' | 'qa' | 'tables' | 'entities' | 'classify' | 'translate' | 'redact' | 'humanize'
  model: string
  // Additional context per operation (e.g., question for qa, target language for translate)
  context?: string
}

export interface ToolConfig {
  tool: string
}

export interface CodeConfig {
  code: string
}

export interface OutputConfig {
  // No config needed
}

export type NodeConfig =
  | InputConfig
  | LlmConfig
  | ResearchConfig
  | DocOpConfig
  | ToolConfig
  | CodeConfig
  | OutputConfig

export type RFNode = Node<{
  config: NodeConfig
  state?: NodeRunState
  onConfigChange?: (config: NodeConfig) => void
}, FlowNodeType>

export type RFEdge = Edge

export interface Flow {
  id: string
  name: string
  nodes: RFNode[]
  edges: RFEdge[]
  createdAt: number
  updatedAt: number
}

export interface NodeRunState {
  status: 'idle' | 'running' | 'done' | 'error'
  output?: unknown
  error?: string
}
