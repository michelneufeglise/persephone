import { Play, Zap, Microscope, FileText, Wrench, Code2, CheckCircle } from 'lucide-react'
import type { FlowNodeType } from '@/types/flows'

export interface NodeMetadata {
  label: string
  description: string
  icon: React.ComponentType<{ className?: string }>
  category: 'Input' | 'Models' | 'Documents' | 'Tools' | 'Code' | 'Output' | 'Research'
  color: string
}

export const nodeMetadata: Record<FlowNodeType, NodeMetadata> = {
  input: {
    label: 'Input',
    description: 'Text or document input',
    icon: Play,
    category: 'Input',
    color: '#2dd4bf',
  },
  llm: {
    label: 'LLM',
    description: 'Language model',
    icon: Zap,
    category: 'Models',
    color: 'var(--accent)',
  },
  research: {
    label: 'Research',
    description: 'Deep research on query',
    icon: Microscope,
    category: 'Research',
    color: '#a78bfa',
  },
  docop: {
    label: 'Document Op',
    description: 'Document operations',
    icon: FileText,
    category: 'Documents',
    color: '#f59e0b',
  },
  tool: {
    label: 'Tool',
    description: 'External tool',
    icon: Wrench,
    category: 'Tools',
    color: '#60a5fa',
  },
  code: {
    label: 'Code',
    description: 'TypeScript execution',
    icon: Code2,
    category: 'Code',
    color: '#34d399',
  },
  output: {
    label: 'Output',
    description: 'Flow result',
    icon: CheckCircle,
    category: 'Output',
    color: '#10b981',
  },
}
