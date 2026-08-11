import { useState, useCallback, useRef, useEffect, createContext, useContext } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge,
  Connection,
  useReactFlow,
  MarkerType,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import './flows.css'
import { Plus, Save, Play, Trash2, ZoomIn, RotateCcw } from 'lucide-react'
import { motion } from 'framer-motion'
import type { Node as RFNode, Edge as RFEdge, NodeTypes } from '@xyflow/react'
import { useAppStore } from '@/store/appStore'
import { nanoid } from '@/store/nanoid'
import type { Flow, FlowNodeType, NodeRunState, NodeConfig } from '@/types/flows'
import { InputNode } from './nodes/InputNode'
import { LlmNode } from './nodes/LlmNode'
import { ResearchNode } from './nodes/ResearchNode'
import { DocOpNode } from './nodes/DocOpNode'
import { ToolNode } from './nodes/ToolNode'
import { CodeNode } from './nodes/CodeNode'
import { OutputNode } from './nodes/OutputNode'
import { NodeLibrary } from './NodeLibrary'
import { runFlow } from './runFlow'

// Context for sharing node state and config updates across all nodes
interface FlowNodeContextValue {
  getNodeState: (id: string) => NodeRunState | undefined
  updateNodeConfig: (id: string, config: NodeConfig) => void
}

const FlowNodeContext = createContext<FlowNodeContextValue>({
  getNodeState: () => undefined,
  updateNodeConfig: () => {},
})

export function useFlowNodeContext() {
  return useContext(FlowNodeContext)
}

const nodeTypes: NodeTypes = {
  input: InputNode,
  llm: LlmNode,
  research: ResearchNode,
  docop: DocOpNode,
  tool: ToolNode,
  code: CodeNode,
  output: OutputNode,
}

function FlowsViewContent() {
  const {
    flows, activeFlowId, createFlow, updateFlow, setFlowNodes, setFlowEdges,
  } = useAppStore()

  const activeFlow = flows.find(f => f.id === activeFlowId)
  const [nodes, setNodes, onNodesChange] = useNodesState((activeFlow?.nodes || []) as any)
  const [edges, setEdges, onEdgesChange] = useEdgesState((activeFlow?.edges || []) as any)
  const [nodeStates, setNodeStates] = useState<Map<string, NodeRunState>>(new Map())
  const [isRunning, setIsRunning] = useState(false)
  const [flowName, setFlowName] = useState(activeFlow?.name || 'Untitled Flow')
  const { getNode, screenToFlowPosition, fitView } = useReactFlow()
  const dragNodeType = useRef<FlowNodeType | null>(null)
  const flowsRootRef = useRef<HTMLDivElement>(null)

  // Reset nodes and edges when active flow changes
  useEffect(() => {
    if (activeFlow) {
      setNodes((activeFlow.nodes || []) as any)
      setEdges((activeFlow.edges || []) as any)
      setFlowName(activeFlow.name)
      setNodeStates(new Map())
    }
  }, [activeFlowId, activeFlow, setNodes, setEdges])

  // Sync nodes and edges to store when they change (but not during flow switch)
  useEffect(() => {
    if (activeFlowId && activeFlow) {
      setFlowNodes(activeFlowId, nodes as any)
      setFlowEdges(activeFlowId, edges as any)
    }
  }, [nodes, edges, activeFlowId, activeFlow, setFlowNodes, setFlowEdges])

  // Update flow name in store
  const handleSaveFlow = useCallback(() => {
    if (activeFlowId) {
      updateFlow(activeFlowId, { name: flowName })
    }
  }, [activeFlowId, flowName, updateFlow])

  // Create new flow
  const handleNewFlow = useCallback(() => {
    const newFlow: Flow = {
      id: nanoid(),
      name: 'New Flow',
      nodes: [],
      edges: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
    }
    createFlow(newFlow)
  }, [createFlow])

  // Run the flow
  const handleRunFlow = useCallback(async () => {
    if (!activeFlow || isRunning) return
    setIsRunning(true)
    setNodeStates(new Map())

    try {
      await runFlow(activeFlow as any, (nodeId, state) => {
        setNodeStates(prev => new Map(prev).set(nodeId, state))
      })
    } catch (error) {
      console.error('Flow execution error:', error)
    } finally {
      setIsRunning(false)
    }
  }, [activeFlow, isRunning])

  // Helper to create and add a node of a given type at a specific position
  const addNodeOfType = useCallback((type: FlowNodeType, position: { x: number; y: number }) => {
    const randomOffset = {
      x: (Math.random() - 0.5) * 80,
      y: (Math.random() - 0.5) * 80,
    }

    const newNode = {
      id: nanoid(),
      type,
      position: {
        x: position.x + randomOffset.x,
        y: position.y + randomOffset.y,
      },
      data: {
        config: getDefaultConfig(type),
      },
    } as RFNode

    setNodes((nds) => [...nds, newNode])
  }, [setNodes])

  // Handle drag over and drop
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }, [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()

    const nodeType = e.dataTransfer.getData('application/reactflow') as FlowNodeType
    if (!nodeType) return

    const position = screenToFlowPosition({
      x: e.clientX,
      y: e.clientY,
    })

    addNodeOfType(nodeType, position)
  }, [screenToFlowPosition, addNodeOfType])

  // Handle click-to-add from library
  const handleAddNode = useCallback((type: FlowNodeType) => {
    if (!flowsRootRef.current) return

    const rect = flowsRootRef.current.getBoundingClientRect()
    const centerPosition = screenToFlowPosition({
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    })

    addNodeOfType(type, centerPosition)
  }, [screenToFlowPosition, addNodeOfType])

  const handleConnect = useCallback((connection: Connection) => {
    setEdges(eds => addEdge(connection, eds))
  }, [setEdges])

  const handleClearCanvas = useCallback(() => {
    if (confirm('Clear all nodes and edges from this flow?')) {
      setNodes([])
      setEdges([])
      setNodeStates(new Map())
    }
  }, [setNodes, setEdges])

  // Context callbacks
  const getNodeState = useCallback((id: string) => {
    return nodeStates.get(id)
  }, [nodeStates])

  const updateNodeConfig = useCallback((id: string, config: NodeConfig) => {
    setNodes(nds => nds.map(nd =>
      nd.id === id ? { ...nd, data: { ...nd.data, config } } : nd,
    ))
  }, [setNodes])

  const isEmpty = nodes.length === 0

  return (
    <FlowNodeContext.Provider value={{ getNodeState, updateNodeConfig }}>
      <div
        ref={flowsRootRef}
        className="relative w-full h-full overflow-hidden bg-[var(--bg-primary)]"
      >
        {/* Canvas */}
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={handleConnect}
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          nodeTypes={nodeTypes}
          fitView
          defaultEdgeOptions={{
            type: 'smoothstep',
            animated: true,
            markerEnd: { type: MarkerType.ArrowClosed },
          }}
          connectionLineStyle={{
            stroke: 'var(--accent)',
            strokeWidth: 2,
          }}
        >
          <Background variant={BackgroundVariant.Dots} color="var(--border)" gap={20} />
          <Controls position="bottom-right" />
          <MiniMap position="bottom-left" />
        </ReactFlow>

      {/* Floating toolbar (top-center) */}
      <motion.div
        initial={{ y: -20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ delay: 0.2 }}
        className="absolute top-4 left-1/2 -translate-x-1/2 z-10"
      >
        <div className="glass rounded-2xl p-3 px-4 shadow-deep flex items-center gap-3 backdrop-blur-md">
          {/* Flow name input */}
          <input
            type="text"
            value={flowName}
            onChange={e => setFlowName(e.target.value)}
            placeholder="Flow name…"
            className="text-xs font-medium text-[var(--text-primary)] bg-transparent border-none outline-none min-w-48 placeholder:text-[var(--text-muted)]"
          />

          {/* Divider */}
          <div className="w-px h-5 bg-[var(--border)]" />

          {/* Flow switcher (if multiple flows) */}
          {flows.length > 1 && (
            <>
              <select
                value={activeFlowId || ''}
                onChange={e => {
                  const flowId = e.target.value
                  if (flowId) {
                    useAppStore.getState().setActiveFlow(flowId)
                  }
                }}
                className="text-xs font-medium text-[var(--text-primary)] bg-[var(--bg-secondary)] border border-[var(--border)] rounded-lg px-2 py-1 outline-none focus:ring-1 focus:ring-[var(--accent)] transition-all"
              >
                {flows.map(f => (
                  <option key={f.id} value={f.id}>{f.name}</option>
                ))}
              </select>
              <div className="w-px h-5 bg-[var(--border)]" />
            </>
          )}

          {/* Action buttons */}
          <button
            onClick={handleNewFlow}
            className="p-2 rounded-lg text-[var(--text-muted)] hover:text-[var(--accent)] hover:bg-[var(--accent-dim)] transition-colors"
            title="New flow"
          >
            <Plus className="w-4 h-4" />
          </button>

          <button
            onClick={handleSaveFlow}
            className="p-2 rounded-lg text-[var(--text-muted)] hover:text-[var(--accent)] hover:bg-[var(--accent-dim)] transition-colors"
            title="Save flow name"
          >
            <Save className="w-4 h-4" />
          </button>

          <button
            onClick={() => fitView()}
            className="p-2 rounded-lg text-[var(--text-muted)] hover:text-[var(--accent)] hover:bg-[var(--accent-dim)] transition-colors"
            title="Fit to view"
          >
            <ZoomIn className="w-4 h-4" />
          </button>

          <button
            onClick={handleClearCanvas}
            className="p-2 rounded-lg text-[var(--text-muted)] hover:text-red-400 hover:bg-red-500/10 transition-colors"
            title="Clear canvas"
          >
            <Trash2 className="w-4 h-4" />
          </button>

          {/* Divider */}
          <div className="w-px h-5 bg-[var(--border)]" />

          {/* Run button */}
          <button
            onClick={handleRunFlow}
            disabled={isRunning || nodes.length === 0}
            className="px-3 py-1.5 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white font-medium text-xs transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
            title="Run flow"
          >
            {isRunning ? (
              <>
                <motion.div
                  animate={{ rotate: 360 }}
                  transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
                  className="flex items-center justify-center"
                >
                  <Play className="w-3 h-3" />
                </motion.div>
                <span>Running…</span>
              </>
            ) : (
              <>
                <Play className="w-3 h-3" />
                <span>Run</span>
              </>
            )}
          </button>
        </div>
      </motion.div>

      {/* Empty state */}
      {isEmpty && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
          className="absolute inset-0 flex items-center justify-center z-5 pointer-events-none"
        >
          <div className="text-center space-y-6">
            <div className="relative w-16 h-16 mx-auto">
              <motion.div
                animate={{ scale: [1, 1.05, 1] }}
                transition={{ duration: 3, repeat: Infinity }}
                className="absolute inset-0 rounded-full bg-gradient-to-br from-[var(--accent-glow)] to-transparent opacity-40"
              />
              <div className="absolute inset-0 flex items-center justify-center text-2xl">
                <span>●</span>
              </div>
            </div>

            <div className="space-y-2">
              <h2 className="text-lg font-display text-[var(--text-primary)]">Design a workflow</h2>
              <p className="text-xs text-[var(--text-muted)] max-w-xs">
                Open the node library and drag a node onto the canvas to get started
              </p>
            </div>
          </div>
        </motion.div>
      )}

        {/* Floating node library drawer */}
        <NodeLibrary
          onDragStart={(type) => { dragNodeType.current = type }}
          onAddNode={handleAddNode}
        />
      </div>
    </FlowNodeContext.Provider>
  )
}

function getDefaultConfig(type: FlowNodeType): any {
  switch (type) {
    case 'input':
      return { kind: 'text', text: '' }
    case 'llm':
      return { model: '', prompt: '' }
    case 'research':
      return {}
    case 'docop':
      return { operation: 'ocr', model: '', context: '' }
    case 'tool':
      return { tool: '' }
    case 'code':
      return { code: '' }
    case 'output':
      return {}
  }
}

export function FlowsView() {
  return (
    <ReactFlowProvider>
      <FlowsViewContent />
    </ReactFlowProvider>
  )
}

export { FlowNodeContext }
