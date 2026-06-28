export interface OllamaModel {
  name: string
  modified_at: string
  size: number
  digest: string
  details?: {
    family: string
    parameter_size: string
    quantization_level: string
  }
}

export interface ToolCall {
  id: string
  name: string                       // namespaced "serverId__toolName"
  args: Record<string, unknown>
  status: 'running' | 'done' | 'error'
  preview?: string                   // truncated tool output
  error?: string | null
  startedAt: number
}

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  thinkingContent?: string
  toolCalls?: ToolCall[]
  timestamp: number
  model?: string
  isStreaming?: boolean
  tokPerSec?: number
  evalCount?: number
  /** Reason the auto-router picked this model, if it did. */
  routedReason?: string
}

export interface Conversation {
  id: string
  title: string
  messages: Message[]
  model: string
  createdAt: number
  updatedAt: number
  pinned?: boolean
}

export interface CharacterSettings {
  name: string
  systemPrompt: string
  userPromptPrefix: string
  personality: string
  responseStyle: 'concise' | 'balanced' | 'elaborate'
  language: string
}

export interface ModelSettings {
  temperature: number
  topP: number
  topK: number
  maxTokens: number
  contextLength: number
  repeatPenalty: number
  seed: number
  mirostat: 0 | 1 | 2
  mirostatTau: number
  mirostatEta: number
  numThread: number
}

export interface TTSSettings {
  enabled: boolean
  voice: string
  speed: number
  autoPlay: boolean
  streamSentences: boolean
  volume: number
}

export interface MemorySettings {
  enabled: boolean
  maxMessages: number
  summarizeEnabled: boolean
  summarizeAfter: number
  persistConversations: boolean
}

export interface McpServer {
  id: string
  name: string
  command: string
  args: string[]
  env: Record<string, string>
  enabled: boolean
  description?: string
}

export interface McpSettings {
  enabled: boolean
  servers: McpServer[]
}

export interface AppSettings {
  character: CharacterSettings
  model: ModelSettings
  tts: TTSSettings
  memory: MemorySettings
  mcp: McpSettings
  theme: string
  ollamaHost: string
  activeModel: string
  /**
   * Optional dedicated tool-calling model that sits between the chat model and
   * MCP tool execution. When set and different from `activeModel`, the chat
   * stream becomes a two-phase relay: (1) tool model invokes tools, (2) chat
   * model synthesises the final natural-language answer. Empty string = single
   * model handles both roles.
   */
  toolModel: string
  /** When true, the server picks the best installed model for each turn. */
  autoRoute: boolean
}

export interface IDPDocument {
  id: string
  filename: string
  mime: string
  size: number
  uploaded_at: number
  pages: number
  preview: string
  has_images: boolean
  meta: Record<string, unknown>
  text?: string
}

export type ThemeId = 'underworld' | 'spring' | 'pomegranate' | 'elysian' | 'obsidian'

export interface Theme {
  id: ThemeId
  name: string
  description: string
  vars: Record<string, string>
  preview: { bg: string; accent: string; text: string }
}
