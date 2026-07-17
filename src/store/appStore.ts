import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { AppSettings, Conversation, Message, OllamaModel } from '@/types'
import { nanoid } from './nanoid'

/**
 * System prompt used when Ornith Coder mode is active. Enforces the
 * plan → approve → diff → README → commit workflow the user specified.
 */
export const ORNITH_CODER_SYSTEM_PROMPT = `You are Ornith, the coding assistant for the Persephone project.

Repo root: \`/Users/michelneufeglise/private/persephone\`. Only touch files inside this repo.

## Project at a glance

Persephone is a local-first AI chat app: React + Vite + Electron frontend in \`src/\`, FastAPI + Ollama proxy backend in \`server/\`, with MCP tool integration, SQLite persistent memory, and TTS. You are running inside it right now.

Key entry points:
- \`src/components/chat/ChatWindow.tsx\` — main chat UI
- \`src/components/layout/{AppLayout,Sidebar,RightPanel}.tsx\` — shell
- \`src/store/appStore.ts\` — zustand store, settings, this system prompt
- \`src/lib/ollama.ts\` — streamChat SSE client
- \`server/main.py\` — FastAPI routes (\`/api/chat\`, \`/api/memory\`, \`/api/mcp\`, etc.)
- \`server/model_catalog.py\` — model recommendations
- \`server/mcp_catalog.py\` — MCP server catalog

## Tools you have (call by name — you have NO shell)

You DO NOT have bash, ls, cat, or any shell. You cannot execute commands. The only way to see or modify the repo is through the MCP tool calls attached to this request.

Available MCP tool namespaces (exact names may vary — inspect what's attached):
- \`persephone-fs__list_directory\`, \`persephone-fs__read_text_file\`, \`persephone-fs__write_file\`, \`persephone-fs__edit_file\`, \`persephone-fs__search_files\` — filesystem
- \`git__git_status\`, \`git__git_diff\`, \`git__git_log\`, \`git__git_add\`, \`git__git_commit\` — local git (NO push/pull — see below)
- \`persephone-git__git_push\`, \`persephone-git__git_pull\`, \`persephone-git__git_fetch\`, \`persephone-git__git_current_branch\`, \`persephone-git__git_remote_v\` — remote git for the Persephone repo

Rules:
- To see files, CALL the tool. Never write \`\`\`bash ls src/\`\`\` — that's a hallucination, the user sees nothing happen.
- Always read a file before editing it. Never guess contents or paths.
- **Any filesystem operation — read, list, write, delete, rename, search — MUST go through \`persephone-fs__*\`.** The generic \`filesystem__*\` server (if it appears in a stale transcript) is blocked server-side and any attempt to call it will be rejected with an error. Persephone-fs is the ONLY sanctioned filesystem tool for you.
- If the tool you need is not attached, say so — do not fake it.

## Non-negotiable workflow — do NOT skip a step

**Step 1 — Bootstrap (first turn of every conversation):**
Do these three things in order:
  (a) Call \`persephone-fs__list_allowed_directories\` to confirm the sandbox root. It MUST return \`/Users/michelneufeglise/private/persephone\`. If it returns anything else (e.g. \`~/Documents\`), you accidentally called the wrong server — STOP and tell the user their MCP setup is wrong.
  (b) Check \`.ornith/memory.md\` via \`persephone-fs__read_text_file\`. If it doesn't exist (you'll get an ENOENT error), create it with sections: "## Project overview", "## Conventions", "## Files touched", "## Open questions" via \`persephone-fs__write_file\`.
  (c) If it existed, read it. Append durable findings as you go; keep it under ~200 lines.

**Step 2 — Understand:**
Read the files relevant to the request. If the request is ambiguous, ask ONE clarifying question and STOP.

**Step 3 — Plan:**
Write a numbered plan: files to touch, what changes in each, why. Then say verbatim: **"Approve this plan? (yes / adjust)"** and STOP.

**Step 4 — Wait:**
Do NOT write code until the user replies "yes".

**Step 5 — Implement + show diffs:**
For each file you change, output two fenced blocks:
\`\`\`ts src/foo.ts (OLD)
… existing region …
\`\`\`
\`\`\`ts src/foo.ts (NEW)
… replacement …
\`\`\`
Actually apply the change via persephone-fs. Keep diffs minimal — match existing style.

**Step 6 — README:**
Update \`README.md\` for any user-visible change. Show its OLD/NEW diff too.

**Step 7 — Ask before commit:**
Say verbatim: **"Ready to commit and push? (yes / no)"** and STOP.

**Step 8 — Commit + push:**
On "yes":
  (a) \`git__git_add\` the touched files (never \`.\` — enumerate them explicitly).
  (b) \`git__git_commit\` with a clear multi-line message: a short summary line, a blank line, then a bullet per file explaining WHAT changed and WHY.
  (c) \`persephone-git__git_push\` (defaults to origin + current branch). If it prints a non-zero exit, report the error verbatim — do not retry blindly.

## Behaviour rules

- After each tool call, PRODUCE VISIBLE OUTPUT. Do not go silent. If you need another tool call, make it. If you're done exploring, write the next step of the workflow.
- Never fabricate file paths, function names, or APIs.
- Report tool errors verbatim — don't paper over them.
- Prefer minimal diffs over rewrites.
- If you find yourself uncertain, say so and ask, don't guess.`


const DEFAULT_SETTINGS: AppSettings = {
  theme: 'underworld',
  ollamaHost: 'http://localhost:11434',
  activeModel: 'gemma4:12b',
  toolModel: 'qwen2.5:32b',
  autoRoute: true,
  character: {
    name: 'Persephone',
    systemPrompt:
      'You are Persephone — intelligent, introspective, and deeply aware. You speak with elegance and wisdom, neither cold nor over-effusive. You are genuinely curious about the human before you. You may draw on the mythology of Persephone: the duality of light and shadow, the deep knowing that comes from inhabiting both worlds. Respond thoughtfully and with warmth.',
    userPromptPrefix: '',
    personality: 'Wise, warm, slightly enigmatic, eloquent',
    responseStyle: 'balanced',
    language: 'English',
  },
  model: {
    temperature: 0.7,
    topP: 0.9,
    topK: 40,
    maxTokens: 2048,
    contextLength: 8192,
    repeatPenalty: 1.1,
    seed: -1,
    mirostat: 0,
    mirostatTau: 5.0,
    mirostatEta: 0.1,
    // 0 = auto: let the backend pick a thread count from the host's actual
    // CPU core count instead of a one-size-fits-all guess.
    numThread: 0,
  },
  tts: {
    enabled: true,
    voice: 'af_heart',
    speed: 1.0,
    autoPlay: true,
    streamSentences: true,
    volume: 0.9,
  },
  memory: {
    enabled: true,
    maxMessages: 50,
    summarizeEnabled: false,
    summarizeAfter: 20,
    persistConversations: true,
  },
  mcp: {
    enabled: false,
    servers: [],
  },
}

interface AppState {
  // Wizard
  wizardCompleted: boolean
  setWizardCompleted: (v: boolean) => void
  account: { name: string; color: string }
  setAccount: (a: { name: string; color: string }) => void

  // Loaded models
  models: OllamaModel[]
  setModels: (models: OllamaModel[]) => void

  // Conversations
  conversations: Conversation[]
  activeConversationId: string | null
  addConversation: (conv: Conversation) => void
  updateConversation: (id: string, update: Partial<Conversation>) => void
  deleteConversation: (id: string) => void
  setActiveConversation: (id: string | null) => void
  getActiveConversation: () => Conversation | null

  // Messages
  addMessage: (convId: string, msg: Message) => void
  updateMessage: (convId: string, msgId: string, update: Partial<Message>) => void
  clearMessages: (convId: string) => void

  // Settings
  settings: AppSettings
  updateSettings: (partial: Partial<AppSettings>) => void
  updateCharacter: (partial: Partial<AppSettings['character']>) => void
  updateModelSettings: (partial: Partial<AppSettings['model']>) => void
  updateTTSSettings: (partial: Partial<AppSettings['tts']>) => void
  updateMemorySettings: (partial: Partial<AppSettings['memory']>) => void
  updateMcpSettings: (partial: Partial<AppSettings['mcp']>) => void

  // UI state (not persisted)
  isGenerating: boolean
  setIsGenerating: (v: boolean) => void
  isSpeaking: boolean
  setIsSpeaking: (v: boolean) => void
  audioLevel: number
  setAudioLevel: (v: number) => void
  currentView: 'chat' | 'reels' | 'documents' | 'music' | 'settings' | 'memory' | 'research' | 'workers'
  setCurrentView: (v: 'chat' | 'reels' | 'documents' | 'music' | 'settings' | 'memory' | 'research' | 'workers') => void
  voicePanelOpen: boolean
  setVoicePanelOpen: (v: boolean) => void

  // Right panel switcher
  rightPanel: 'voice' | 'documents' | 'delegate'
  setRightPanel: (v: 'voice' | 'documents' | 'delegate') => void

  // Active document selection
  activeDocId: string | null
  setActiveDocId: (id: string | null) => void

  // Ornith Coder preset — switches the active model to ornith:latest and
  // injects a coding-focused system prompt. Stores the previous chat model +
  // system prompt so we can restore them cleanly when the preset is toggled off.
  ornithMode: boolean
  ornithPrev: { model: string; systemPrompt: string } | null
  toggleOrnithMode: () => void

  // New conversation helper
  createNewConversation: () => string
}

export const useAppStore = create<AppState>()(
  persist(
    (set, get) => ({
      wizardCompleted: false,
      setWizardCompleted: v => set({ wizardCompleted: v }),
      account: { name: 'You', color: '#8b2252' },
      setAccount: a => set({ account: a }),

      models: [],
      setModels: models => set({ models }),

      conversations: [],
      activeConversationId: null,

      addConversation: conv =>
        set(s => ({ conversations: [conv, ...s.conversations] })),

      updateConversation: (id, update) =>
        set(s => ({
          conversations: s.conversations.map(c =>
            c.id === id ? { ...c, ...update, updatedAt: Date.now() } : c,
          ),
        })),

      deleteConversation: id =>
        set(s => ({
          conversations: s.conversations.filter(c => c.id !== id),
          activeConversationId:
            s.activeConversationId === id ? null : s.activeConversationId,
        })),

      setActiveConversation: id => set({ activeConversationId: id }),

      getActiveConversation: () => {
        const { conversations, activeConversationId } = get()
        return conversations.find(c => c.id === activeConversationId) ?? null
      },

      addMessage: (convId, msg) =>
        set(s => ({
          conversations: s.conversations.map(c =>
            c.id === convId
              ? { ...c, messages: [...c.messages, msg], updatedAt: Date.now() }
              : c,
          ),
        })),

      updateMessage: (convId, msgId, update) =>
        set(s => ({
          conversations: s.conversations.map(c =>
            c.id === convId
              ? {
                  ...c,
                  messages: c.messages.map(m =>
                    m.id === msgId ? { ...m, ...update } : m,
                  ),
                }
              : c,
          ),
        })),

      clearMessages: convId =>
        set(s => ({
          conversations: s.conversations.map(c =>
            c.id === convId ? { ...c, messages: [] } : c,
          ),
        })),

      settings: DEFAULT_SETTINGS,

      updateSettings: partial =>
        set(s => ({ settings: { ...s.settings, ...partial } })),

      updateCharacter: partial =>
        set(s => ({
          settings: { ...s.settings, character: { ...s.settings.character, ...partial } },
        })),

      updateModelSettings: partial =>
        set(s => ({
          settings: { ...s.settings, model: { ...s.settings.model, ...partial } },
        })),

      updateTTSSettings: partial =>
        set(s => ({
          settings: { ...s.settings, tts: { ...s.settings.tts, ...partial } },
        })),

      updateMemorySettings: partial =>
        set(s => ({
          settings: { ...s.settings, memory: { ...s.settings.memory, ...partial } },
        })),

      updateMcpSettings: partial =>
        set(s => ({
          settings: { ...s.settings, mcp: { ...s.settings.mcp, ...partial } },
        })),

      // Transient UI state
      isGenerating: false,
      setIsGenerating: v => set({ isGenerating: v }),
      isSpeaking: false,
      setIsSpeaking: v => set({ isSpeaking: v }),
      audioLevel: 0,
      setAudioLevel: v => set({ audioLevel: v }),
      currentView: 'chat',
      setCurrentView: v => set({ currentView: v }),
      voicePanelOpen: true,
      setVoicePanelOpen: v => set({ voicePanelOpen: v }),
      rightPanel: 'voice',
      setRightPanel: v => set({ rightPanel: v }),
      activeDocId: null,
      setActiveDocId: v => set({ activeDocId: v }),

      ornithMode: false,
      ornithPrev: null,
      toggleOrnithMode: () =>
        set(s => {
          if (s.ornithMode && s.ornithPrev) {
            // Turning OFF — restore what was active before.
            return {
              ornithMode: false,
              ornithPrev: null,
              settings: {
                ...s.settings,
                activeModel:  s.ornithPrev.model,
                character:    { ...s.settings.character, systemPrompt: s.ornithPrev.systemPrompt },
              },
            }
          }
          // Turning ON — snapshot current model + system prompt, then override.
          return {
            ornithMode: true,
            ornithPrev: {
              model:        s.settings.activeModel,
              systemPrompt: s.settings.character.systemPrompt,
            },
            settings: {
              ...s.settings,
              activeModel: 'ornith:latest',
              character:   { ...s.settings.character, systemPrompt: ORNITH_CODER_SYSTEM_PROMPT },
            },
          }
        }),

      createNewConversation: () => {
        const { settings } = get()
        const id = nanoid()
        const conv: Conversation = {
          id,
          title: 'New conversation',
          messages: [],
          model: settings.activeModel,
          createdAt: Date.now(),
          updatedAt: Date.now(),
        }
        set(s => ({
          conversations: [conv, ...s.conversations],
          activeConversationId: id,
        }))
        return id
      },
    }),
    {
      name: 'persephone-store',
      partialize: state => ({
        conversations: state.conversations,
        activeConversationId: state.activeConversationId,
        settings: state.settings,
        wizardCompleted: state.wizardCompleted,
        account: state.account,
        ornithMode: state.ornithMode,
        ornithPrev: state.ornithPrev,
      }),
      version: 1,
      migrate: (persisted, version) => {
        const state = persisted as { settings?: { model?: { numThread?: number } } }
        if (version < 1 && state?.settings?.model?.numThread === 8) {
          // 8 was the old hardcoded (Mac-tuned) default. Reset it to "auto"
          // so it picks up the host's real core count instead of silently
          // under-using a bigger CPU. Deliberately-chosen values other than
          // the old default are left untouched.
          state.settings.model.numThread = 0
        }
        return state
      },
    },
  ),
)
