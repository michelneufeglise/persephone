import { useAppStore } from '@/store/appStore'
import { Toggle } from '@/components/ui/Toggle'
import { Slider } from '@/components/ui/Slider'
import { Panel } from '@/components/ui/Panel'
import { Button } from '@/components/ui/Button'
import { Trash2, Sparkles } from 'lucide-react'

export function MemorySection() {
  const { settings, updateMemorySettings, conversations, deleteConversation, setWizardCompleted } = useAppStore()
  const mem = settings.memory

  function clearAll() {
    if (window.confirm('Delete all conversations? This cannot be undone.')) {
      conversations.forEach(c => deleteConversation(c.id))
    }
  }

  const totalMessages = conversations.reduce((sum, c) => sum + c.messages.length, 0)

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h3 className="font-serif text-xl text-[var(--text-primary)] mb-1">Memory</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Conversation storage and context management.
        </p>
      </div>

      <div className="grid grid-cols-3 gap-3">
        {[
          { label: 'Conversations', value: conversations.length },
          { label: 'Total Messages', value: totalMessages },
          { label: 'Context Limit', value: `${settings.model.contextLength.toLocaleString()} tk` },
        ].map(stat => (
          <Panel key={stat.label} className="p-3 text-center">
            <div className="text-2xl font-serif text-[var(--accent)]">{stat.value}</div>
            <div className="text-xs text-[var(--text-muted)] mt-1">{stat.label}</div>
          </Panel>
        ))}
      </div>

      <Panel className="p-4 space-y-4">
        <Toggle
          checked={mem.enabled}
          onChange={v => updateMemorySettings({ enabled: v })}
          label="Persistent Memory"
          description="Save conversations to local storage between sessions"
        />
        <Toggle
          checked={mem.persistConversations}
          onChange={v => updateMemorySettings({ persistConversations: v })}
          label="Keep conversation history"
          description="Maintain full history in the sidebar"
        />
        <Toggle
          checked={mem.summarizeEnabled}
          onChange={v => updateMemorySettings({ summarizeEnabled: v })}
          label="Auto-summarize long threads"
          description="Compress old messages to fit context window"
        />
      </Panel>

      <Panel className="p-4 space-y-4">
        <Slider
          label="Max messages in context"
          value={mem.maxMessages}
          min={5} max={100} step={5}
          onChange={v => updateMemorySettings({ maxMessages: v })}
          format={v => String(v)}
        />
        {mem.summarizeEnabled && (
          <Slider
            label="Summarize after N messages"
            value={mem.summarizeAfter}
            min={5} max={50} step={5}
            onChange={v => updateMemorySettings({ summarizeAfter: v })}
            format={v => String(v)}
          />
        )}
      </Panel>

      <div className="flex gap-3 flex-wrap">
        <Button variant="danger" size="sm" onClick={clearAll} className="flex items-center gap-2">
          <Trash2 className="w-4 h-4" />
          Clear All Conversations
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={async () => {
            await fetch('/api/setup/reset', { method: 'POST' }).catch(() => {})
            setWizardCompleted(false)
          }}
          className="flex items-center gap-2"
        >
          <Sparkles className="w-4 h-4" />
          Re-run Setup Wizard
        </Button>
      </div>
    </div>
  )
}
