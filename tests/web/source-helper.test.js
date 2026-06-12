/**
 * P0 foundations — loadAppHtml() contract.
 *
 * The helper must return the single-file view of the app regardless of
 * whether the split has happened yet: full CSS tokens, full JS, full markup,
 * and no remaining LOCAL <link rel=stylesheet> / <script src> tags
 * (CDN tags stay). These assertions are deliberately split-agnostic so this
 * spec is green before AND after each extraction task.
 */
import { describe, it, expect } from 'vitest'
import { loadAppHtml } from './_source.js'

describe('loadAppHtml() — single-file source reconstruction', () => {
  const html = loadAppHtml()

  it('contains the design tokens (CSS layer present)', () => {
    expect(html).toContain('--green')
    expect(html).toContain('--surface')
    expect(html).toMatch(/html\.dark/)
  })

  it('contains the app JS (script layer present)', () => {
    expect(html).toMatch(/function showToast\(/)
    expect(html).toMatch(/function buildTrackCard\(/)
    expect(html).toMatch(/getElementById\(['"]disc-v2-grid['"]\)/)
  })

  it('contains the markup (document layer present)', () => {
    expect(html).toContain('id="drop-zone"')
    expect(html).toContain('id="track-list"')
    expect(html).toContain('id="toast-stack"')
  })

  it('has no un-inlined local stylesheets or scripts', () => {
    const localLinks = [...html.matchAll(/<link\b[^>]*rel=["']stylesheet["'][^>]*>/gi)]
      .filter(m => { const h = m[0].match(/href=["']([^"']+)["']/i); return h && !/^https?:|^\/\//.test(h[1]) })
    const localScripts = [...html.matchAll(/<script\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/gi)]
      .filter(m => !/^https?:|^\/\//.test(m[1]))
    expect(localLinks).toEqual([])
    expect(localScripts).toEqual([])
  })

  it('keeps CDN tags untouched', () => {
    expect(html).toContain('cdnjs.cloudflare.com/ajax/libs/jsmediatags')
    expect(html).toContain('cdn.tailwindcss.com')
  })
})
