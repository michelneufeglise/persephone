export interface Skill {
  name: string
  description: string
  category: string
  source: 'builtin' | 'user'
  path: string
  keywords: string[]
  default_enabled: boolean
  enabled: boolean
  body_preview?: string
  body?: string
}

export interface SkillsList {
  skills: Skill[]
  max_active: number
}

export async function listSkills(): Promise<SkillsList> {
  const r = await fetch('/api/skills')
  if (!r.ok) return { skills: [], max_active: 3 }
  return await r.json()
}

export async function getSkill(name: string): Promise<Skill | null> {
  const r = await fetch(`/api/skills/${encodeURIComponent(name)}`)
  if (!r.ok) return null
  return await r.json()
}

export async function setSkillEnabled(name: string, enabled: boolean): Promise<boolean> {
  const r = await fetch(`/api/skills/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  return r.ok
}
