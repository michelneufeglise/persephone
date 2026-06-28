import type { OllamaModel, Message, ModelSettings } from '@/types'

export async function fetchModels(): Promise<OllamaModel[]> {
  const res = await fetch('/api/models')
  if (!res.ok) throw new Error('Failed to fetch models')
  const data = await res.json()
  return data.models ?? []
}

export interface TokStats {
  evalCount: number
  evalDurationMs: number
  tokPerSec: number
  promptCount: number
  promptTokPerSec: number
}

export interface ToolEvent {
  phase: 'start' | 'end'
  id: string
  name: string
  args?: Record<string, unknown>
  preview?: string
  error?: string | null
}

export interface StreamChunk {
  content: string
  done: boolean
  error?: string
  stats?: TokStats
  toolEvent?: ToolEvent
  thinking?: string
  /** Emitted once at the start when the server auto-routed to a model. */
  route?: { model: string; reason: string }
}

export async function* streamChat(
  model: string,
  messages: Message[],
  systemPrompt: string,
  modelSettings: ModelSettings,
  signal?: AbortSignal,
  toolModel?: string,
  convId?: string,
  userMsgId?: string,
  autoRoute?: boolean,
): AsyncGenerator<StreamChunk> {
  const ollamaMessages = [
    ...(systemPrompt ? [{ role: 'system', content: systemPrompt }] : []),
    ...messages.map(m => ({ role: m.role, content: m.content })),
  ]

  // M1-optimised options merged with user settings
  const options = {
    temperature:    modelSettings.temperature,
    top_p:          modelSettings.topP,
    top_k:          modelSettings.topK,
    num_predict:    modelSettings.maxTokens,
    num_ctx:        modelSettings.contextLength,
    repeat_penalty: modelSettings.repeatPenalty,
    num_thread:     modelSettings.numThread,
    num_batch:      512,
    f16_kv:         true,
    use_mmap:       true,
    ...(modelSettings.seed >= 0 ? { seed: modelSettings.seed } : {}),
    ...(modelSettings.mirostat > 0
      ? {
          mirostat:     modelSettings.mirostat,
          mirostat_tau: modelSettings.mirostatTau,
          mirostat_eta: modelSettings.mirostatEta,
        }
      : {}),
  }

  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model,
      messages: ollamaMessages,
      options,
      tool_model:  toolModel || '',
      conv_id:     convId    || '',
      user_msg_id: userMsgId || '',
      auto_route:  !!autoRoute,
    }),
    signal,
  })

  if (!res.ok) {
    yield { content: '', done: true, error: `HTTP ${res.status}` }
    return
  }

  const reader = res.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const payload = line.slice(6).trim()
      if (payload === '[DONE]') {
        yield { content: '', done: true }
        return
      }
      try {
        const obj = JSON.parse(payload)
        if (obj.error) {
          yield { content: '', done: true, error: obj.error }
          return
        }

        // Custom MCP tool-call events from the backend
        if (obj.tool_event) {
          yield {
            content: '',
            done: false,
            toolEvent: {
              phase: obj.tool_event as 'start' | 'end',
              id: obj.id,
              name: obj.name,
              args: obj.args,
              preview: obj.preview,
              error: obj.error,
            },
          }
          continue
        }

        // Backend-injected native/delegated thinking deltas
        if (typeof obj.thinking === 'string' && obj.thinking) {
          yield { content: '', done: false, thinking: obj.thinking }
          continue
        }

        // Auto-route decision (one-shot, sent before the first content chunk)
        if (typeof obj.route === 'string' && obj.route) {
          yield {
            content: '', done: false,
            route: { model: obj.route, reason: obj.reason ?? '' },
          }
          continue
        }

        const content = obj.message?.content ?? ''
        const isDone = obj.done === true

        if (isDone) {
          // Extract performance stats from final chunk
          const evalCount = obj.eval_count ?? 0
          const evalDurationNs = obj.eval_duration ?? 1
          const promptCount = obj.prompt_eval_count ?? 0
          const promptDurationNs = obj.prompt_eval_duration ?? 1
          const stats: TokStats = {
            evalCount,
            evalDurationMs: Math.round(evalDurationNs / 1e6),
            tokPerSec: evalCount > 0 ? Math.round(evalCount / (evalDurationNs / 1e9)) : 0,
            promptCount,
            promptTokPerSec: promptCount > 0 ? Math.round(promptCount / (promptDurationNs / 1e9)) : 0,
          }
          yield { content, done: true, stats }
          return
        }

        yield { content, done: false }
      } catch {
        // malformed line
      }
    }
  }
}

export function parseThinking(text: string): { thinking: string; response: string } {
  const m = text.match(/^<think>([\s\S]*?)<\/think>([\s\S]*)$/s)
  if (m) return { thinking: m[1].trim(), response: m[2].trim() }
  return { thinking: '', response: text }
}
