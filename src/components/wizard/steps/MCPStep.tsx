import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Check, ExternalLink, Globe, Folder, Database, Brain, Code, Wrench, AlertCircle } from 'lucide-react'
import { clsx } from 'clsx'

interface McpServer {
  id: string
  name: string
  description: string
  category: string
  tags: string[]
  requires_setup: boolean
  docs_url: string
  install: {
    command: string
    args: string[]
    env_vars: Record<string, { required: boolean; description: string }>
  }
}

interface McpCategory {
  id: string
  name: string
  icon: string
}

interface MCPStepProps {
  selected: string[]
  onChange: (ids: string[]) => void
}

const ICONS: Record<string, React.ElementType> = {
  globe: Globe, folder: Folder, database: Database,
  brain: Brain, code: Code,
}

export function MCPStep({ selected, onChange }: MCPStepProps) {
  const [servers, setServers] = useState<McpServer[]>([])
  const [categories, setCategories] = useState<McpCategory[]>([])
  const [activeCat, setActiveCat] = useState<string>('all')

  useEffect(() => {
    fetch('/api/mcp/catalog').then(r => r.json()).then(d => {
      setServers(d.servers ?? [])
      setCategories(d.categories ?? [])
    }).catch(() => {})
  }, [])

  function toggle(id: string) {
    onChange(selected.includes(id) ? selected.filter(x => x !== id) : [...selected, id])
  }

  const visible = activeCat === 'all'
    ? servers
    : servers.filter(s => s.category === activeCat)

  // Sort: no-setup first, then those needing setup
  const sorted = [...visible].sort((a, b) => {
    if (a.requires_setup !== b.requires_setup) return a.requires_setup ? 1 : -1
    return 0
  })

  return (
    <div className="max-w-3xl mx-auto space-y-5">
      <div>
        <h2 className="font-serif text-2xl text-[var(--text-primary)] mb-1">Tools (MCP Servers)</h2>
        <p className="text-sm text-[var(--text-muted)]">
          Give Persephone access to the web, your files, databases, and more. All servers below are <strong>free and open-source</strong>.
        </p>
      </div>

      {/* Category tabs */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <button
          onClick={() => setActiveCat('all')}
          className={clsx(
            'px-3 py-1.5 rounded-full text-xs font-medium border transition-all',
            activeCat === 'all'
              ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
              : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-bright)]',
          )}
        >
          <Wrench className="w-3 h-3 inline mr-1" />All
        </button>
        {categories.map(c => {
          const Icon = ICONS[c.icon] ?? Wrench
          return (
            <button
              key={c.id}
              onClick={() => setActiveCat(c.id)}
              className={clsx(
                'px-3 py-1.5 rounded-full text-xs font-medium border transition-all',
                activeCat === c.id
                  ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                  : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-bright)]',
              )}
            >
              <Icon className="w-3 h-3 inline mr-1" />{c.name}
            </button>
          )
        })}
      </div>

      {/* Server cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-h-[400px] overflow-y-auto pr-1"
        style={{ scrollbarWidth: 'thin', scrollbarColor: 'var(--scrollbar) transparent' }}>
        {sorted.map(s => {
          const isSelected = selected.includes(s.id)
          return (
            <motion.div
              key={s.id}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              onClick={() => toggle(s.id)}
              className={clsx(
                'relative rounded-xl border p-3.5 cursor-pointer transition-all duration-200',
                isSelected
                  ? 'border-[var(--accent)] bg-[var(--accent-dim)] shadow-md shadow-[var(--accent-glow)]'
                  : 'border-[var(--border)] bg-[var(--bg-tertiary)] hover:border-[var(--border-bright)]',
              )}
            >
              <div className="flex items-start justify-between gap-2 mb-1.5">
                <span className="text-sm font-semibold text-[var(--text-primary)]">{s.name}</span>
                {isSelected && (
                  <div className="w-5 h-5 rounded-full bg-[var(--accent)] flex items-center justify-center flex-shrink-0">
                    <Check className="w-3 h-3 text-white" />
                  </div>
                )}
              </div>
              <p className="text-xs text-[var(--text-secondary)] leading-relaxed mb-2.5">{s.description}</p>

              <div className="flex flex-wrap gap-1 mb-2">
                {s.tags.map(t => (
                  <span key={t} className="text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--bg-secondary)] text-[var(--text-muted)] border border-[var(--border)]">
                    {t}
                  </span>
                ))}
              </div>

              <div className="flex items-center justify-between gap-2 text-[11px]">
                {s.requires_setup ? (
                  <span className="flex items-center gap-1 text-amber-400">
                    <AlertCircle className="w-3 h-3" />Needs API key / path
                  </span>
                ) : (
                  <span className="text-green-400">Ready to use</span>
                )}
                <a
                  href={s.docs_url}
                  target="_blank"
                  rel="noreferrer"
                  onClick={e => e.stopPropagation()}
                  className="flex items-center gap-0.5 text-[var(--accent)] hover:underline"
                >
                  Docs <ExternalLink className="w-2.5 h-2.5" />
                </a>
              </div>
            </motion.div>
          )
        })}
      </div>

      <p className="text-xs text-[var(--text-muted)]">
        Selected {selected.length} of {servers.length}. You can add more from Settings → Tools later.
      </p>
    </div>
  )
}
