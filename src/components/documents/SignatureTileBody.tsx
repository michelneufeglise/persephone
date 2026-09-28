import { clsx } from 'clsx'
import { AlertTriangle, Cpu, ExternalLink } from 'lucide-react'
import type { SignatureCrop, SignatureFeature, SignatureTileData } from '@/lib/docAgent'
import { friendlyDocName } from './kgFormat'

/**
 * Body of the "Signature verification" tile: score gauge (band colour), the
 * questioned crop next to the reference crops, key reasons, the per-feature
 * breakdown and the Signature model's visual assessment. Theme-aware: colours
 * come from CSS variables, band colours are translucent so they read on both
 * light and dark themes; the crops sit on a paper-white card (they are photos).
 */

const BAND_STYLE: Record<SignatureTileData['band'], { text: string; bg: string; bar: string; label: string }> = {
  consistent: { text: 'text-emerald-500', bg: 'bg-emerald-500/15 border-emerald-500/30', bar: 'rgb(16 185 129)', label: 'Consistent' },
  inconclusive: { text: 'text-amber-500', bg: 'bg-amber-500/15 border-amber-500/30', bar: 'rgb(245 158 11)', label: 'Inconclusive' },
  inconsistent: { text: 'text-red-500', bg: 'bg-red-500/15 border-red-500/30', bar: 'rgb(239 68 68)', label: 'Inconsistent' },
}

const VERDICT_DOT: Record<SignatureFeature['verdict'], string> = {
  consistent: 'bg-emerald-500',
  borderline: 'bg-amber-500',
  different: 'bg-red-500',
}

function fmt(n: number | null | undefined, digits = 3): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—'
  return Math.abs(n) >= 10 ? n.toFixed(1) : n.toFixed(digits)
}

function ScoreGauge({ score, band }: { score: number; band: SignatureTileData['band'] }) {
  const s = Math.max(0, Math.min(100, score))
  const style = BAND_STYLE[band] ?? BAND_STYLE.inconclusive
  return (
    <div className="space-y-1">
      <div className="relative h-2.5 rounded-full overflow-hidden flex" aria-hidden>
        <div className="h-full bg-red-500/20" style={{ width: '40%' }} />
        <div className="h-full bg-amber-500/20" style={{ width: '30%' }} />
        <div className="h-full bg-emerald-500/20" style={{ width: '30%' }} />
        <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${s}%`, background: style.bar, opacity: 0.85 }} />
      </div>
      <div className="relative h-3 text-[9.5px] font-mono text-[var(--text-muted)]">
        {[0, 40, 70, 100].map(t => (
          <span
            key={t}
            className="absolute top-0"
            style={{ left: `${t}%`, transform: t === 0 ? undefined : t === 100 ? 'translateX(-100%)' : 'translateX(-50%)' }}
          >
            {t}
          </span>
        ))}
      </div>
    </div>
  )
}

function Crop({ crop, label, large = false }: { crop: SignatureCrop; label: string; large?: boolean }) {
  return (
    <a
      href={crop.url}
      target="_blank"
      rel="noopener noreferrer"
      title={`${label}${crop.doc ? ` · ${crop.doc}` : ''} — open full size`}
      className={clsx(
        'group relative block rounded-md overflow-hidden border border-[var(--glass-stroke)] bg-white',
        'hover:border-[var(--accent)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--accent)]',
      )}
    >
      <img
        src={crop.url}
        alt={label}
        loading="lazy"
        className={clsx('w-full object-contain', large ? 'h-20' : 'h-12')}
      />
      <span className="absolute top-0.5 left-1 text-[9px] font-mono font-semibold text-neutral-500">{label}</span>
    </a>
  )
}

export function SignatureTileBody({ data }: { data: SignatureTileData }) {
  const style = BAND_STYLE[data.band] ?? BAND_STYLE.inconclusive
  const refs = data.references ?? []
  const breakdown = data.breakdown ?? []
  return (
    <div className="space-y-3 border-t border-[var(--glass-stroke)] pt-2.5 min-w-0">
      {/* Score */}
      <div className="flex items-center gap-3">
        <div className={clsx('text-2xl font-semibold tabular-nums leading-none', style.text)}>{data.score}%</div>
        <div className="min-w-0 flex-1">
          <span className={clsx('inline-block text-[10.5px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded border', style.bg, style.text)}>
            {style.label}
          </span>
          <div className="text-xs text-[var(--text-secondary)] mt-0.5 truncate" title={data.band_label}>
            {data.band_label}
          </div>
        </div>
      </div>
      <ScoreGauge score={data.score} band={data.band} />

      {/* Crops */}
      {(data.questioned || refs.length > 0) && (
        <div className="grid grid-cols-[minmax(0,1.1fr)_minmax(0,1.9fr)] gap-2 items-start">
          <div className="space-y-1 min-w-0">
            <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">Questioned</div>
            {data.questioned && <Crop crop={data.questioned} label="Q" large />}
            {data.questioned_doc && (
              <div className="text-[10px] text-[var(--text-muted)] truncate" title={data.questioned_doc}>{friendlyDocName(data.questioned_doc)}</div>
            )}
          </div>
          <div className="space-y-1 min-w-0">
            <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
              References ({refs.length})
            </div>
            <div className="grid grid-cols-3 gap-1">
              {refs.slice(0, 9).map((r, i) => (
                <Crop key={r.url} crop={r} label={`R${i + 1}`} />
              ))}
            </div>
          </div>
        </div>
      )}
      {data.candidates && data.candidates.count > 1 && (
        <div className="text-[10.5px] text-[var(--text-muted)]">
          Signature = line {data.candidates.picked} of {data.candidates.count} at the bottom of the page · picked by{' '}
          {data.candidates.picked_by === 'model' ? data.model || 'the signature model' : 'the layout heuristic'}
          {data.candidates.sheet && (
            <>
              {' · '}
              <a href={data.candidates.sheet} target="_blank" rel="noopener noreferrer" className="text-[var(--accent)] hover:underline inline-flex items-center gap-0.5">
                candidates <ExternalLink className="w-2.5 h-2.5" />
              </a>
            </>
          )}
        </div>
      )}

      {/* Reasons + warnings */}
      {data.reasons?.length > 0 && (
        <ul className="space-y-0.5">
          {data.reasons.slice(0, 4).map((r, i) => (
            <li key={i} className="text-xs text-[var(--text-secondary)] flex gap-1.5">
              <span className={clsx('mt-1.5 w-1 h-1 rounded-full flex-shrink-0', style.text.replace('text-', 'bg-'))} />
              <span>{r}</span>
            </li>
          ))}
        </ul>
      )}
      {data.warnings?.length > 0 && (
        <div className="space-y-0.5">
          {data.warnings.slice(0, 4).map((w, i) => (
            <div key={i} className="text-[11px] text-amber-500 flex gap-1.5 items-start">
              <AlertTriangle className="w-3 h-3 flex-shrink-0 mt-0.5" />
              <span>{w}</span>
            </div>
          ))}
        </div>
      )}

      {/* Per-feature breakdown */}
      {breakdown.length > 0 && (
        <details className="text-xs group" open>
          <summary className="cursor-pointer text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors font-medium select-none">
            Feature breakdown
          </summary>
          <div className="mt-1.5 overflow-x-auto rounded border border-[var(--glass-stroke)]" style={{ scrollbarWidth: 'thin' }}>
            <table className="text-[11px] min-w-full border-separate border-spacing-0">
              <thead>
                <tr>
                  {['Feature', 'Dist.', 'Spread', 'z', ''].map((h, j) => (
                    <th
                      key={h || j}
                      className={clsx(
                        'px-1.5 py-1 font-medium text-[var(--text-secondary)] whitespace-nowrap border-b border-[var(--glass-stroke)]',
                        j === 0 ? 'text-left' : 'text-right',
                      )}
                      style={{ background: 'var(--bg-glass-strong)' }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {breakdown.map((b, i) => (
                  <tr key={b.feature} className={i % 2 === 1 ? 'bg-[var(--glass-fill)]' : undefined} title={`${b.label}: ${b.verdict}`}>
                    <td className="px-1.5 py-0.5 text-[var(--text-primary)] leading-tight">{b.label}</td>
                    <td className="px-1.5 py-0.5 text-right tabular-nums text-[var(--text-secondary)]">{fmt(b.questioned_distance)}</td>
                    <td className="px-1.5 py-0.5 text-right tabular-nums text-[var(--text-muted)]">{fmt(b.reference_spread)}</td>
                    <td className="px-1.5 py-0.5 text-right tabular-nums text-[var(--text-primary)] whitespace-nowrap">{b.z > 0 ? '+' : ''}{fmt(b.z, 1)}</td>
                    <td className="px-1.5 py-0.5 text-right">
                      <span className={clsx('inline-block w-2 h-2 rounded-full', VERDICT_DOT[b.verdict] ?? 'bg-[var(--text-muted)]')} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="text-[10px] text-[var(--text-muted)] mt-1">
            Dist. = questioned signature's distance to the references · Spread = the references' own variation · z = how far the questioned signature is from the references, in units of their natural variation (<span className="text-emerald-500">●</span> &lt;1 consistent · <span className="text-amber-500">●</span> &lt;2.5 borderline · <span className="text-red-500">●</span> different)
          </div>
        </details>
      )}

      {/* Visual assessment by the Signature model */}
      {data.assessment && (
        <details className="text-xs">
          <summary className="cursor-pointer text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors font-medium select-none">
            Visual assessment{data.model ? ` · ${data.model}` : ''}
          </summary>
          <p className="text-xs text-[var(--text-muted)] mt-1.5 p-2 rounded border border-[var(--glass-stroke)] whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
            {data.assessment}
          </p>
        </details>
      )}

      <div className="flex items-center gap-1.5 text-[10px] text-[var(--text-muted)] flex-wrap">
        <Cpu className="w-3 h-3" />
        <span>{data.engine || 'local signature engine'}</span>
        {data.model && (
          <>
            <span>·</span>
            <span className="font-mono">{data.model}</span>
          </>
        )}
      </div>
      {data.caveat && <div className="text-[10px] italic text-[var(--text-muted)]">{data.caveat}</div>}
    </div>
  )
}
