import { useCallback, useEffect, useRef, useState } from 'react'
import { Upload, Loader2, X } from 'lucide-react'
import type { NodeProps } from '@xyflow/react'
import type { InputConfig, NodeRunState } from '@/types/flows'
import * as flowActions from '@/lib/flowActions'
import { useFlowNodeContext } from '../FlowsView'
import { NodeShell } from '../NodeShell'

interface InputNodeProps extends NodeProps {
  data: { config: InputConfig }
}

export function InputNode({ id, data, selected }: InputNodeProps) {
  const context = useFlowNodeContext()
  const nodeState = context.getNodeState(id)
  const [docs, setDocs] = useState<Array<{ id: string; name: string }>>([])
  const [isUploading, setIsUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [uploadedFileName, setUploadedFileName] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    flowActions.fetchDocuments().then(setDocs).catch(() => {})
  }, [])

  const handleChange = useCallback((updates: Partial<InputConfig>) => {
    const newConfig = { ...data.config, ...updates }
    context.updateNodeConfig(id, newConfig)
  }, [id, data.config, context])

  const handleFileUpload = useCallback(async (file: File) => {
    setIsUploading(true)
    setUploadError('')
    setUploadedFileName('')

    try {
      const formData = new FormData()
      formData.append('file', file)

      const res = await fetch('/api/idp/upload', {
        method: 'POST',
        body: formData,
      })

      if (!res.ok) {
        throw new Error(`Upload failed: ${res.status}`)
      }

      const uploadedDoc = await res.json()
      const docId = uploadedDoc.id || uploadedDoc.document_id

      if (!docId) {
        throw new Error('No document ID in response')
      }

      handleChange({ documentId: docId })
      setUploadedFileName(file.name)

      const freshDocs = await flowActions.fetchDocuments()
      setDocs(freshDocs)
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setIsUploading(false)
    }
  }, [handleChange])

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      handleFileUpload(file)
    }
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const handleUploadClick = () => {
    fileInputRef.current?.click()
  }

  return (
    <NodeShell
      nodeId={id}
      type="input"
      runState={nodeState}
      isSelected={selected}
      handles={{ target: false, source: true }}
    >
      <div className="space-y-3">
        {/* Kind selector */}
        <div className="space-y-1.5">
          <label className="text-xs text-[var(--text-muted)] block font-medium">Kind</label>
          <select
            value={data.config.kind}
            onChange={e => handleChange({ kind: e.target.value as 'text' | 'document' })}
            className="w-full px-2 py-1.5 rounded-lg bg-[var(--bg-secondary)] text-xs text-[var(--text-primary)] border border-[var(--border)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)] transition-all"
          >
            <option value="text">Text</option>
            <option value="document">Document</option>
          </select>
        </div>

        {/* Text input */}
        {data.config.kind === 'text' ? (
          <div className="space-y-1.5">
            <label className="text-xs text-[var(--text-muted)] block font-medium">Text</label>
            <textarea
              value={data.config.text || ''}
              onChange={e => handleChange({ text: e.target.value })}
              placeholder="Enter input text…"
              className="w-full px-2 py-2 rounded-lg bg-[var(--bg-secondary)] text-xs text-[var(--text-primary)] border border-[var(--border)] font-mono resize-none h-24 focus:outline-none focus:ring-1 focus:ring-[var(--accent)] transition-all"
            />
          </div>
        ) : (
          <div className="space-y-1.5">
            {/* Document selector */}
            <label className="text-xs text-[var(--text-muted)] block font-medium">Document</label>
            <select
              value={data.config.documentId || ''}
              onChange={e => handleChange({ documentId: e.target.value })}
              className="w-full px-2 py-1.5 rounded-lg bg-[var(--bg-secondary)] text-xs text-[var(--text-primary)] border border-[var(--border)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)] transition-all"
            >
              <option value="">Select a document…</option>
              {docs.map(d => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>

            {/* Upload button */}
            <button
              onClick={handleUploadClick}
              disabled={isUploading}
              className="w-full px-2 py-2 rounded-lg bg-[var(--bg-secondary)] text-xs text-[var(--accent)] border border-[var(--border)] hover:bg-[var(--accent-dim)] hover:border-[var(--accent)] transition-colors disabled:opacity-50 flex items-center justify-center gap-2 font-medium"
            >
              {isUploading ? (
                <>
                  <Loader2 className="w-3 h-3 animate-spin" />
                  Uploading…
                </>
              ) : (
                <>
                  <Upload className="w-3 h-3" />
                  Upload file…
                </>
              )}
            </button>

            {/* Upload feedback */}
            {uploadedFileName && (
              <div className="text-xs text-green-400 bg-[var(--bg-secondary)] p-2 rounded-lg border border-green-400/20 flex items-center justify-between">
                <span className="truncate">{uploadedFileName}</span>
                <button
                  onClick={() => setUploadedFileName('')}
                  className="ml-2 p-0.5 hover:bg-green-400/20 rounded"
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            )}

            {uploadError && (
              <div className="text-xs text-red-400 bg-[var(--bg-secondary)] p-2 rounded-lg border border-red-400/20 flex items-center justify-between">
                <span className="truncate">{uploadError}</span>
                <button
                  onClick={() => setUploadError('')}
                  className="ml-2 p-0.5 hover:bg-red-400/20 rounded"
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            )}

            {/* Hidden file input */}
            <input
              ref={fileInputRef}
              type="file"
              onChange={handleFileInputChange}
              accept=".pdf,.docx,.xlsx,.csv,.txt,.md,image/*"
              className="hidden"
            />
          </div>
        )}
      </div>
    </NodeShell>
  )
}
