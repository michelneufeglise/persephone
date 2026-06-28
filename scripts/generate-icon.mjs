#!/usr/bin/env node
/**
 * Generates build/icon.png (1024×1024) and build/icon.icns from an inline
 * SVG that mirrors the in-app Persephone orb (conic-gradient sphere + glyph).
 * electron-builder picks up build/icon.* automatically.
 */
import sharp from 'sharp'
import { spawnSync } from 'node:child_process'
import { mkdir, writeFile, rm } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BUILD = path.join(ROOT, 'build')

// macOS rounds icons via a squircle mask itself; keep the SVG fully filled.
const SVG = (size) => `
<svg width="${size}" height="${size}" viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <radialGradient id="orb" cx="35%" cy="30%" r="80%">
      <stop offset="0%"   stop-color="#ffffff" stop-opacity="0.55"/>
      <stop offset="18%"  stop-color="#ef4d83"/>
      <stop offset="55%"  stop-color="#d6356a"/>
      <stop offset="78%"  stop-color="#3a0a6b"/>
      <stop offset="100%" stop-color="#06040c"/>
    </radialGradient>
    <radialGradient id="halo" cx="50%" cy="50%" r="60%">
      <stop offset="0%"   stop-color="#7df9ff" stop-opacity="0.55"/>
      <stop offset="70%"  stop-color="#7df9ff" stop-opacity="0.0"/>
    </radialGradient>
    <linearGradient id="ring" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%"   stop-color="#d6356a"/>
      <stop offset="60%"  stop-color="#7df9ff"/>
      <stop offset="100%" stop-color="#3a0a2c"/>
    </linearGradient>
  </defs>

  <!-- midnight backdrop -->
  <rect width="1024" height="1024" fill="#06040c"/>
  <!-- subtle deep vignette -->
  <circle cx="512" cy="512" r="600" fill="url(#halo)" opacity="0.6"/>
  <!-- holographic ring -->
  <circle cx="512" cy="512" r="380" fill="none" stroke="url(#ring)" stroke-width="6" opacity="0.7"/>
  <!-- main orb -->
  <circle cx="512" cy="512" r="340" fill="url(#orb)"/>
  <!-- inner highlight crescent -->
  <ellipse cx="430" cy="410" rx="160" ry="80" fill="white" opacity="0.18"/>
  <!-- glyph: stylised flower (matches in-app ⚘) -->
  <g fill="#ffffff" opacity="0.92" transform="translate(512 540)">
    <circle r="42" />
    <circle cx="0"   cy="-95" r="58" fill="#fbe5ef"/>
    <circle cx="82"  cy="-48" r="58" fill="#fbe5ef"/>
    <circle cx="82"  cy="48"  r="58" fill="#fbe5ef"/>
    <circle cx="0"   cy="95"  r="58" fill="#fbe5ef"/>
    <circle cx="-82" cy="48"  r="58" fill="#fbe5ef"/>
    <circle cx="-82" cy="-48" r="58" fill="#fbe5ef"/>
  </g>
</svg>`.trim()

const SIZES = [16, 32, 64, 128, 256, 512, 1024]

async function main() {
  await mkdir(BUILD, { recursive: true })

  // 1. Master 1024px PNG (electron-builder uses this for icon.icns + linux/win)
  const masterPath = path.join(BUILD, 'icon.png')
  await sharp(Buffer.from(SVG(1024))).png().toFile(masterPath)
  console.log(`✓ ${path.relative(ROOT, masterPath)}`)

  // 2. Build a proper .icns via macOS' iconutil for sharper appearance in Finder
  if (process.platform === 'darwin' && hasCmd('iconutil')) {
    const iconset = path.join(BUILD, 'icon.iconset')
    if (existsSync(iconset)) await rm(iconset, { recursive: true, force: true })
    await mkdir(iconset, { recursive: true })

    for (const sz of SIZES) {
      const png = await sharp(Buffer.from(SVG(sz))).png().toBuffer()
      await writeFile(path.join(iconset, `icon_${sz}x${sz}.png`), png)
      if (sz < 1024) {
        const png2 = await sharp(Buffer.from(SVG(sz * 2))).png().toBuffer()
        await writeFile(path.join(iconset, `icon_${sz}x${sz}@2x.png`), png2)
      }
    }
    const r = spawnSync('iconutil', ['-c', 'icns', iconset, '-o', path.join(BUILD, 'icon.icns')])
    if (r.status === 0) {
      console.log(`✓ ${path.relative(ROOT, path.join(BUILD, 'icon.icns'))}`)
      await rm(iconset, { recursive: true, force: true })
    } else {
      console.warn('iconutil failed — electron-builder will derive .icns from icon.png')
    }
  }
}

function hasCmd(cmd) {
  return spawnSync('which', [cmd]).status === 0
}

main().catch(err => {
  console.error(err)
  process.exit(1)
})
