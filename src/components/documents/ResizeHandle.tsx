import { useRef, useEffect, useState, useCallback } from 'react'

interface ResizeHandleProps {
  onWidthChange: (width: number) => void
  currentWidth: number
  minWidth: number
  maxWidth: number
  containerWidth: number
}

export function ResizeHandle({
  onWidthChange,
  currentWidth,
  minWidth,
  maxWidth,
  containerWidth,
}: ResizeHandleProps) {
  const handleRef = useRef<HTMLDivElement>(null)
  const [isDragging, setIsDragging] = useState(false)
  const startXRef = useRef(0)
  const startWidthRef = useRef(currentWidth)

  const handlePointerDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    if (handleRef.current) {
      handleRef.current.setPointerCapture(e.pointerId)
    }
    setIsDragging(true)
    startXRef.current = e.clientX
    startWidthRef.current = currentWidth
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'
  }, [currentWidth])

  const handlePointerUp = useCallback((e: React.PointerEvent) => {
    if (isDragging) {
      if (handleRef.current) {
        handleRef.current.releasePointerCapture(e.pointerId)
      }
      setIsDragging(false)
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
    }
  }, [isDragging])

  const handlePointerMove = useCallback((e: React.PointerEvent) => {
    if (!isDragging) return

    // Use requestAnimationFrame to batch updates
    requestAnimationFrame(() => {
      const delta = startXRef.current - e.clientX
      let newWidth = startWidthRef.current + delta

      // Clamp to min/max
      newWidth = Math.max(minWidth, Math.min(maxWidth, newWidth))

      onWidthChange(newWidth)
    })
  }, [isDragging, minWidth, maxWidth, onWidthChange])

  const handleDoubleClick = useCallback(() => {
    onWidthChange(340) // Reset to default
  }, [onWidthChange])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'ArrowLeft') {
      e.preventDefault()
      const delta = e.shiftKey ? -60 : -20
      let newWidth = currentWidth + delta
      newWidth = Math.max(minWidth, Math.min(maxWidth, newWidth))
      onWidthChange(newWidth)
    } else if (e.key === 'ArrowRight') {
      e.preventDefault()
      const delta = e.shiftKey ? 60 : 20
      let newWidth = currentWidth + delta
      newWidth = Math.max(minWidth, Math.min(maxWidth, newWidth))
      onWidthChange(newWidth)
    }
  }, [currentWidth, minWidth, maxWidth, onWidthChange])

  // Handle pointer move and up on document
  useEffect(() => {
    const handleDocumentPointerMove = (e: PointerEvent) => {
      if (isDragging) {
        const delta = startXRef.current - e.clientX
        let newWidth = startWidthRef.current + delta
        newWidth = Math.max(minWidth, Math.min(maxWidth, newWidth))
        onWidthChange(newWidth)
      }
    }

    const handleDocumentPointerUp = (e: PointerEvent) => {
      if (isDragging) {
        setIsDragging(false)
        document.body.style.userSelect = ''
        document.body.style.cursor = ''
      }
    }

    if (isDragging) {
      document.addEventListener('pointermove', handleDocumentPointerMove)
      document.addEventListener('pointerup', handleDocumentPointerUp)
      return () => {
        document.removeEventListener('pointermove', handleDocumentPointerMove)
        document.removeEventListener('pointerup', handleDocumentPointerUp)
      }
    }
  }, [isDragging, minWidth, maxWidth, onWidthChange])

  return (
    <div
      ref={handleRef}
      role="separator"
      aria-orientation="vertical"
      aria-valuenow={currentWidth}
      aria-valuemin={minWidth}
      aria-valuemax={maxWidth}
      tabIndex={0}
      onPointerDown={handlePointerDown}
      onPointerUp={handlePointerUp}
      onPointerMove={handlePointerMove}
      onDoubleClick={handleDoubleClick}
      onKeyDown={handleKeyDown}
      className="group flex-shrink-0 w-1 hover:w-2 flex items-center justify-center cursor-col-resize transition-all"
      style={{ cursor: isDragging ? 'col-resize' : 'col-resize' }}
    >
      <div className="w-1 h-8 rounded-full bg-[var(--border)] group-hover:bg-[var(--accent)] group-hover:h-8 transition-colors" />
    </div>
  )
}
