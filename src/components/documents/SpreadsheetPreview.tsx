import { useCallback, useEffect, useRef, useState } from 'react'
import { FileSpreadsheet, Loader2, AlertCircle, EyeOff, RefreshCw } from 'lucide-react'
import { clsx } from 'clsx'
import { fetchSheetPreview } from '@/lib/docAgent'
import type { SheetCell, SheetListEntry } from '@/lib/docAgent'

const PAGE = 200

interface SpreadsheetPreviewProps {
  docId: string
  filename: string
  /** Max height of the scrollable table area (CSS length or px). */
  maxHeight?: number | string
  className?: string
}

interface SheetState {
  sheet: string | null
  columns: string[]
  dtypes: string[]
  formula: string[]
  rows: SheetCell[][]
  total: number
}

const EMPTY: SheetState = { sheet: null, columns: [], dtypes: [], formula: [], rows: [], total: 0 }

function formatCell(v: SheetCell, dtype: string | undefined): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) return String(v)
    if (dtype === 'number' && Math.abs(v) >= 1000 && Number.isInteger(v)) return v.toLocaleString()
    if (!Number.isInteger(v)) return v.toLocaleString(undefined, { maximumFractionDigits: 6 })
    return String(v)
  }
  if (typeof v === 'boolean') return v ? 'TRUE' : 'FALSE'
  return v
}

/**
 * Read-only spreadsheet viewer: sheet tabs, sticky header, zebra rows,
 * right-aligned numeric columns and paged "Load more" (200 rows at a time).
 */
export function SpreadsheetPreview({ docId, filename, maxHeight = 360, className }: SpreadsheetPreviewProps) {
  const [sheets, setSheets] = useState<SheetListEntry[]>([])
  const [hiddenCount, setHiddenCount] = useState(0)
  const [state, setState] = useState<SheetState>(EMPTY)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const reqRef = useRef(0)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  const load = useCallback(async (sheet: string | null) => {
    const id = ++reqRef.current
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl
    setLoading(true)
    setError(null)
    try {
      const r = await fetchSheetPreview(docId, sheet, 0, PAGE, ctrl.signal)
      if (id !== reqRef.current) return
      // Hidden sheets are not requested (the server omits them); only their count is shown.
      setSheets(r.sheets.filter(s => !s.hidden))
      setHiddenCount(Math.max(0, Number(r.hidden_sheets) || 0))
      setState({
        sheet: r.sheet,
        columns: r.columns,
        dtypes: r.dtypes,
        formula: r.formula_columns ?? [],
        rows: r.rows,
        total: r.total,
      })
      scrollRef.current?.scrollTo({ top: 0, left: 0 })
    } catch (e) {
      if (id !== reqRef.current || (e instanceof DOMException && e.name === 'AbortError')) return
      setError(e instanceof Error ? e.message : 'Could not load the sheet')
    } finally {
      if (id === reqRef.current) setLoading(false)
    }
  }, [docId])

  useEffect(() => {
    setSheets([])
    setHiddenCount(0)
    setState(EMPTY)
    void load(null)
    return () => abortRef.current?.abort()
  }, [load])

  async function loadMore() {
    if (!state.sheet || loadingMore) return
    const id = reqRef.current
    setLoadingMore(true)
    try {
      const r = await fetchSheetPreview(docId, state.sheet, state.rows.length, PAGE)
      if (id !== reqRef.current) return
      setState(s => (s.sheet === r.sheet ? { ...s, rows: [...s.rows, ...r.rows], total: r.total } : s))
    } catch (e) {
      if (id === reqRef.current) setError(e instanceof Error ? e.message : 'Could not load more rows')
    } finally {
      setLoadingMore(false)
    }
  }

  const totalRows = sheets.reduce((n, s) => n + s.rows, 0)
  const format = (filename.split('.').pop() || 'sheet').toUpperCase()
  const badge = [
    format,
    `${sheets.length} sheet${sheets.length === 1 ? '' : 's'}`,
    `${totalRows.toLocaleString()} row${totalRows === 1 ? '' : 's'}`,
  ].join(' · ')
  const remaining = Math.max(0, state.total - state.rows.length)

  return (
    <div className={clsx('rounded-xl glass-card overflow-hidden flex flex-col min-h-0', className)}>
      {/* Header: badge + sheet tabs */}
      <div className="flex items-center gap-3 px-3 pt-2.5 border-b border-[var(--glass-stroke)] min-w-0">
        <div className="flex items-center gap-1.5 pb-2 flex-shrink-0">
          <FileSpreadsheet className="w-4 h-4 text-[var(--accent)]" />
          <span
            className="text-[10px] font-medium tracking-wide px-1.5 py-0.5 rounded bg-[var(--accent-dim)] text-[var(--accent)] whitespace-nowrap"
            title={hiddenCount > 0 ? `${hiddenCount} hidden sheet${hiddenCount === 1 ? '' : 's'} not counted` : undefined}
          >
            {sheets.length > 0 ? badge : format}
          </span>
        </div>
        <div
          role="tablist"
          aria-label="Sheets"
          className="flex items-end gap-0.5 overflow-x-auto min-w-0 flex-1"
          style={{ scrollbarWidth: 'none' }}
        >
          {sheets.map(s => {
            const active = s.name === state.sheet
            return (
              <button
                key={s.name}
                role="tab"
                aria-selected={active}
                onClick={() => !active && void load(s.name)}
                title={`${s.name} · ${s.rows.toLocaleString()} rows × ${s.cols} columns`}
                className={clsx(
                  'flex items-center gap-1 px-2.5 pb-2 pt-1 text-xs whitespace-nowrap border-b-2 -mb-px transition-colors',
                  active
                    ? 'border-[var(--accent)] text-[var(--accent)] font-medium'
                    : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text-secondary)]',
                )}
              >
                {s.name}
              </button>
            )
          })}
        </div>
        {hiddenCount > 0 && (
          <span
            className="flex items-center gap-1 pb-2 text-[10px] text-[var(--text-muted)] whitespace-nowrap flex-shrink-0"
            title="Hidden sheets in this workbook are not shown"
          >
            <EyeOff className="w-3 h-3" />
            {hiddenCount} hidden sheet{hiddenCount === 1 ? '' : 's'}
          </span>
        )}
      </div>

      {/* Body */}
      {error ? (
        <div className="flex items-center justify-between gap-2 px-4 py-3 text-xs text-red-500 bg-red-500/10">
          <span className="flex items-center gap-2 min-w-0">
            <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
            <span className="break-words">{error}</span>
          </span>
          <button
            onClick={() => void load(state.sheet)}
            className="flex items-center gap-1 text-[var(--text-muted)] hover:text-[var(--accent)] flex-shrink-0"
          >
            <RefreshCw className="w-3 h-3" /> Retry
          </button>
        </div>
      ) : loading && state.rows.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-10 text-xs text-[var(--text-muted)]">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading sheet…
        </div>
      ) : state.columns.length === 0 ? (
        <div className="py-8 text-center text-xs text-[var(--text-muted)]">This sheet is empty</div>
      ) : (
        <div
          ref={scrollRef}
          className={clsx('overflow-auto relative', loading && 'opacity-60 transition-opacity')}
          style={{ maxHeight, scrollbarWidth: 'thin' }}
        >
          <table className="text-xs border-separate border-spacing-0 min-w-full">
            <thead>
              <tr>
                <th
                  className="sticky top-0 left-0 z-20 px-2 py-1.5 text-right font-normal text-[10px] text-[var(--text-muted)] border-b border-r border-[var(--glass-stroke)]"
                  style={{ background: 'var(--bg-glass-strong)' }}
                >
                  #
                </th>
                {state.columns.map((c, j) => {
                  const numeric = state.dtypes[j] === 'number'
                  const isFormula = state.formula.includes(c)
                  return (
                    <th
                      key={j}
                      scope="col"
                      title={`${c} · ${state.dtypes[j] ?? 'text'}${isFormula ? ' · formula' : ''}`}
                      className={clsx(
                        'sticky top-0 z-10 px-3 py-1.5 font-medium text-[var(--text-secondary)] whitespace-nowrap border-b border-[var(--glass-stroke)]',
                        numeric ? 'text-right' : 'text-left',
                      )}
                      style={{ background: 'var(--bg-glass-strong)' }}
                    >
                      {isFormula && <span className="mr-1 font-mono italic text-[10px] text-[var(--accent)]">ƒx</span>}
                      {c}
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {state.rows.map((row, i) => (
                <tr key={i} className={i % 2 === 1 ? 'bg-[var(--glass-fill)]' : undefined}>
                  <td className="px-2 py-1 text-right text-[10px] text-[var(--text-muted)] tabular-nums border-r border-[var(--glass-stroke)] select-none">
                    {i + 1}
                  </td>
                  {state.columns.map((_, j) => {
                    const v = row[j] ?? null
                    const numeric = state.dtypes[j] === 'number' || typeof v === 'number'
                    const text = formatCell(v, state.dtypes[j])
                    return (
                      <td
                        key={j}
                        title={text.length > 40 ? text : undefined}
                        className={clsx(
                          'px-3 py-1 text-[var(--text-primary)] whitespace-nowrap max-w-[280px] truncate',
                          numeric && 'text-right tabular-nums',
                        )}
                      >
                        {text}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Footer */}
      {!error && state.columns.length > 0 && (
        <div className="flex items-center justify-between gap-2 px-3 py-1.5 border-t border-[var(--glass-stroke)] text-[11px] text-[var(--text-muted)]">
          <span className="tabular-nums">
            {state.rows.length.toLocaleString()} of {state.total.toLocaleString()} rows · {state.columns.length} columns
          </span>
          {remaining > 0 && (
            <button
              onClick={() => void loadMore()}
              disabled={loadingMore}
              className="flex items-center gap-1 px-2 py-0.5 rounded-md glass-card glass-card-hover text-[var(--text-secondary)] hover:text-[var(--accent)] disabled:opacity-50"
            >
              {loadingMore && <Loader2 className="w-3 h-3 animate-spin" />}
              Load more ({Math.min(PAGE, remaining).toLocaleString()})
            </button>
          )}
        </div>
      )}
    </div>
  )
}
