export type ScheduleKind = 'once' | 'daily' | 'weekly' | 'every_n_min' | 'cron'

export interface PlannedTask {
  id:                  string
  name:                string
  prompt:              string
  model:               string
  scheduleKind:        ScheduleKind
  scheduleValue:       string
  scheduleDescription: string
  toolIds:             string[]
  skillNames:          string[]
  enabled:             boolean
  nextRunTs:           number | null   // seconds
  nextRunAt:           number | null   // ms
  lastRunTs:           number | null
  lastStatus:          '' | 'running' | 'succeeded' | 'failed'
  lastConvId:          string
  createdAt:           number
  updatedAt:           number
}

export interface PlannedTaskRun {
  id:            string
  taskId:        string
  startedAt:    number
  finishedAt:   number | null
  status:        'running' | 'succeeded' | 'failed'
  convId:        string
  outputPreview: string
  error:         string
}

export interface PlannerAvailable {
  mcp_tools: { id: string; name: string; description: string }[]
  skills:    { name: string; description: string }[]
  models:    string[]
}

export interface PlannedTaskCreate {
  name:          string
  prompt:        string
  model:         string
  scheduleKind:  ScheduleKind
  scheduleValue: string
  toolIds:       string[]
  skillNames:    string[]
  enabled:       boolean
}

export type PlannedTaskPatch = Partial<PlannedTaskCreate>

export async function listTasks(): Promise<PlannedTask[]> {
  const r = await fetch('/api/planner/tasks')
  if (!r.ok) return []
  return (await r.json()).tasks ?? []
}

export async function getTask(id: string): Promise<(PlannedTask & { runs: PlannedTaskRun[] }) | null> {
  const r = await fetch(`/api/planner/tasks/${encodeURIComponent(id)}`)
  if (!r.ok) return null
  return await r.json()
}

async function throwOnError(r: Response): Promise<never> {
  let msg = `HTTP ${r.status}`
  try {
    const body = await r.json()
    if (typeof body?.detail === 'string') msg = body.detail
  } catch { /* not JSON */ }
  throw new Error(msg)
}

export async function createTask(body: PlannedTaskCreate): Promise<PlannedTask> {
  const r = await fetch('/api/planner/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) return throwOnError(r)
  return (await r.json()).task
}

export async function patchTask(id: string, patch: PlannedTaskPatch): Promise<PlannedTask> {
  const r = await fetch(`/api/planner/tasks/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!r.ok) return throwOnError(r)
  return (await r.json()).task
}

export async function deleteTask(id: string): Promise<boolean> {
  const r = await fetch(`/api/planner/tasks/${encodeURIComponent(id)}`, { method: 'DELETE' })
  return r.ok
}

export async function runTaskNow(id: string): Promise<{ ok: boolean; conv_id: string; error?: string | null }> {
  const r = await fetch(`/api/planner/tasks/${encodeURIComponent(id)}/run`, { method: 'POST' })
  if (!r.ok) return { ok: false, conv_id: '', error: `HTTP ${r.status}` }
  return await r.json()
}

export async function getAvailable(): Promise<PlannerAvailable> {
  const r = await fetch('/api/planner/available')
  if (!r.ok) return { mcp_tools: [], skills: [], models: [] }
  return await r.json()
}

/**
 * Fetch a conversation the planner created server-side and shape it into
 * the frontend's `Conversation` type so it can be dropped into the local
 * Zustand store. Returns null if the conversation doesn't exist.
 *
 * The planner writes convs via `_db.upsert_conversation` + `upsert_message`,
 * but the frontend's `conversations` array is a purely-local Zustand slice
 * (persisted to localStorage). Without this hydration step, tasks would
 * post their results into a conv the sidebar / tabs never see.
 */
export async function fetchServerConversation(convId: string): Promise<{
  id: string; title: string; model: string
  createdAt: number; updatedAt: number
  messages: {
    id: string; role: 'user' | 'assistant' | 'system' | 'tool'
    content: string; thinkingContent?: string
    model: string; timestamp: number
    meta?: Record<string, unknown>
  }[]
} | null> {
  const r = await fetch(`/api/memory/conversations/${encodeURIComponent(convId)}`)
  if (!r.ok) return null
  return await r.json()
}

