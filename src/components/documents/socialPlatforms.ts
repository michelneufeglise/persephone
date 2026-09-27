/**
 * Social platforms the Documents agent's web lookup can target (mirrors
 * PLATFORMS in server/doc_web.py). Pure — no React imports — so kgEntities /
 * kgModel / kgPipeline can use it too.
 */

export type SocialPlatform = 'linkedin' | 'facebook' | 'instagram' | 'x'

export interface SocialPlatformInfo {
  id: SocialPlatform
  label: string
  /** Accent colour for icons / node tints (theme-aware for X). */
  color: string
  /** Short badge text shown next to search results. */
  badge: string
  /** Badge background (CSS `background` value). */
  badgeBg: string
  domains: string[]
  profilePatterns: RegExp[]
  excludePatterns: RegExp[]
}

export const SOCIAL_PLATFORMS: Record<SocialPlatform, SocialPlatformInfo> = {
  linkedin: {
    id: 'linkedin',
    label: 'LinkedIn',
    color: '#0a66c2',
    badge: 'in',
    badgeBg: '#0a66c2',
    domains: ['linkedin.com'],
    profilePatterns: [/linkedin\.com\/in\//i],
    excludePatterns: [/\/pub\/dir\//i, /\/search\//i, /\/posts?\//i, /\/pulse\//i],
  },
  facebook: {
    id: 'facebook',
    label: 'Facebook',
    color: '#1877f2',
    badge: 'f',
    badgeBg: '#1877f2',
    domains: ['facebook.com', 'fb.com'],
    profilePatterns: [
      /facebook\.com\/(?!public\/|pages\/category|search|groups\/|events\/|watch|marketplace)[A-Za-z0-9.-]+\/?$/i,
      /facebook\.com\/profile\.php\?id=\d+/i,
    ],
    excludePatterns: [/\/public\//i, /\/posts\//i, /\/photos\//i, /\/videos\//i],
  },
  instagram: {
    id: 'instagram',
    label: 'Instagram',
    color: '#e1306c',
    badge: 'IG',
    badgeBg: 'linear-gradient(45deg, #f58529, #dd2a7b 50%, #8134af)',
    domains: ['instagram.com'],
    profilePatterns: [/instagram\.com\/[A-Za-z0-9._]+\/?$/i],
    excludePatterns: [/\/p\//i, /\/reel\//i, /\/explore\//i],
  },
  x: {
    id: 'x',
    label: 'X',
    color: 'var(--text-primary)',
    badge: '𝕏',
    badgeBg: '#000000',
    domains: ['x.com', 'twitter.com'],
    profilePatterns: [/(?:^|[/.])(?:x|twitter)\.com\/[A-Za-z0-9_]+\/?$/i],
    excludePatterns: [/\/status\//i, /\/search/i, /\/hashtag\//i, /\.com\/(?:home|explore|i|intent|share|login|signup)\/?$/i],
  },
}

function hostnameOf(url: string): string {
  if (!url) return ''
  const u = url.includes('://') ? url : `https://${url}`
  try {
    return new URL(u).hostname.toLowerCase()
  } catch {
    return ''
  }
}

/** "facebook" / "twitter" / … → SocialPlatform (null for anything else, incl. "web"). */
export function asPlatform(value: unknown): SocialPlatform | null {
  if (typeof value !== 'string') return null
  const v = value.trim().toLowerCase()
  if (v === 'twitter') return 'x'
  return v in SOCIAL_PLATFORMS ? (v as SocialPlatform) : null
}

/** Platform of a URL by domain (subdomains included), or null. */
export function platformOfUrl(url: string | null | undefined): SocialPlatform | null {
  const host = hostnameOf(url || '')
  if (!host) return null
  for (const p of Object.values(SOCIAL_PLATFORMS)) {
    if (p.domains.some(d => host === d || host.endsWith(`.${d}`))) return p.id
  }
  return null
}

/** Explicit platform (e.g. item.platform) first, else derived from the URL. */
export function resolvePlatform(platform: unknown, url?: string | null): SocialPlatform | null {
  return asPlatform(platform) ?? platformOfUrl(url)
}

/** True when the URL looks like a personal profile on its social platform. */
export function isSocialProfileUrl(url: string | null | undefined): boolean {
  const pid = platformOfUrl(url)
  if (!pid || !url) return false
  const p = SOCIAL_PLATFORMS[pid]
  const full = url.trim().split('#')[0]
  const noQuery = full.split('?')[0]
  if (p.excludePatterns.some(re => re.test(full))) return false
  return p.profilePatterns.some(re => re.test(full) || re.test(noQuery))
}

/** Display label for a target id ("linkedin" → "LinkedIn", "web" → "Web"). */
export function targetLabel(target: string): string {
  const p = asPlatform(target)
  if (p) return SOCIAL_PLATFORMS[p].label
  const t = target.trim()
  return t.toLowerCase() === 'web' ? 'Web' : t
}

/** "linkedin, facebook" (the Laya decision value) → "LinkedIn + Facebook". */
export function targetsLabel(value: string | null | undefined): string {
  if (!value || value === 'none') return ''
  return value
    .split(',')
    .map(t => t.trim())
    .filter(Boolean)
    .map(targetLabel)
    .join(' + ')
}
