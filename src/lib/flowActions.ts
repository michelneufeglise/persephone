import type {
  LlmConfig, ResearchConfig, DocOpConfig, ToolConfig, CodeConfig,
} from '@/types/flows'

export async function fetchModelsForFlow(): Promise<string[]> {
  const res = await fetch('/api/models')
  if (!res.ok) return []
  const data = await res.json()
  return (data.models ?? []).map((m: any) => m.name)
}

export async function fetchTools(): Promise<Array<{ name: string; description: string }>> {
  const res = await fetch('/api/mcp/tools')
  if (!res.ok) return []
  const data = await res.json()
  return (data.tools ?? []).map((t: any) => ({ name: t.name, description: t.description || '' }))
}

export async function fetchDocuments(): Promise<Array<{ id: string; name: string }>> {
  const res = await fetch('/api/idp/documents')
  if (!res.ok) return []
  const data = await res.json()
  return (data.documents ?? []).map((d: any) => ({ id: d.id, name: d.filename || d.id }))
}

export async function runLlm(config: LlmConfig, input: unknown): Promise<string> {
  if (!config.model) {
    throw new Error('Select a model for the LLM node')
  }

  const inputText = typeof input === 'string' ? input : JSON.stringify(input)
  const res = await fetch('/api/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: config.model,
      prompt: inputText,
      system: config.prompt,
      stream: false,
    }),
  })
  if (!res.ok) throw new Error(`LLM failed: ${res.status}`)
  const data = await res.json()
  return data.response || ''
}

export async function runResearch(config: ResearchConfig, input: unknown): Promise<string> {
  const query = typeof input === 'string' ? input : JSON.stringify(input)

  return new Promise((resolve, reject) => {
    const eventSource = new EventSource(`/api/research/start?query=${encodeURIComponent(query)}`)
    let finalReport = ''

    eventSource.onmessage = (e) => {
      if (e.data === '[DONE]') {
        eventSource.close()
        resolve(finalReport)
        return
      }
      try {
        const event = JSON.parse(e.data)
        if (event.phase === 'done' && event.reportMd) {
          finalReport = event.reportMd
        }
      } catch {
        // ignore parse errors
      }
    }

    eventSource.onerror = () => {
      eventSource.close()
      reject(new Error('Research stream failed'))
    }
  })
}

export async function runDocOp(config: DocOpConfig, input: unknown): Promise<unknown> {
  const docId = typeof input === 'string' ? input : (input as any)?.documentId
  if (!docId) throw new Error('DocOp requires document ID as input')

  let body: any = { doc_id: docId, options: {} }

  switch (config.operation) {
    case 'qa':
      body.options.question = config.context || ''
      break
    case 'translate':
      body.options.target = config.context || 'en'
      break
    case 'redact':
      body.options.categories = config.context?.split(',').map((c: string) => c.trim()) || []
      break
    case 'humanize':
      const [tone, intensity] = (config.context || '').split(':')
      body.options.tone = tone || 'friendly'
      body.options.intensity = intensity || 'medium'
      break
    case 'summarize':
      body.options.style = config.context || 'brief'
      break
  }

  const res = await fetch(`/api/idp/${config.operation}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`DocOp ${config.operation} failed: ${res.status}`)
  const data = await res.json()
  return data
}

export async function runTool(config: ToolConfig, input: unknown): Promise<string> {
  const args = typeof input === 'object' ? input : { value: input }
  const res = await fetch('/api/mcp/tools/call', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: config.tool,
      arguments: args,
    }),
  })
  if (!res.ok) throw new Error(`Tool call failed: ${res.status}`)
  const data = await res.json()
  return data.result || ''
}

export async function runCode(config: CodeConfig, input: unknown): Promise<unknown> {
  const res = await fetch('/api/flows/run-code', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      code: config.code,
      input,
    }),
  })
  if (!res.ok) throw new Error(`Code execution failed: ${res.status}`)
  const data = await res.json()
  if (!data.ok) throw new Error(data.error || 'Code execution error')
  return data.output
}
