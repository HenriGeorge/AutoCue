/**
 * Regression tests for UX audit PR B — Issue 7 (sticky bar context),
 * M-4 (Saved view destination).
 *
 * The Issue 6 banner-collapse is verified via live screenshots — the
 * pill style is CSS-only and jsdom can't snapshot pixels.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

/* ====================================== Issue 7: sticky bar context */

function switchTabBarVisibility(tabName) {
  const dlBar = document.getElementById('download-bar')
  if (dlBar) {
    if (tabName === 'cues') dlBar.classList.remove('hidden-by-tab')
    else dlBar.classList.add('hidden-by-tab')
  }
  document.body.setAttribute('data-active-tab', tabName)
}

describe('switchTab hides download-bar on non-Cues tabs', () => {
  beforeEach(() => {
    document.body.innerHTML = `<div id="download-bar" class="visible"></div>`
  })

  it('shows download-bar on Cues tab', () => {
    switchTabBarVisibility('cues')
    expect(document.getElementById('download-bar').classList.contains('hidden-by-tab')).toBe(false)
  })

  it('hides download-bar on Library tab', () => {
    switchTabBarVisibility('library')
    expect(document.getElementById('download-bar').classList.contains('hidden-by-tab')).toBe(true)
  })

  it('hides download-bar on Discover tab', () => {
    switchTabBarVisibility('discover')
    expect(document.getElementById('download-bar').classList.contains('hidden-by-tab')).toBe(true)
  })

  it('tags <body> with data-active-tab', () => {
    switchTabBarVisibility('discover')
    expect(document.body.getAttribute('data-active-tab')).toBe('discover')
  })

  it('switching back to Cues re-shows the bar', () => {
    switchTabBarVisibility('discover')
    expect(document.getElementById('download-bar').classList.contains('hidden-by-tab')).toBe(true)
    switchTabBarVisibility('cues')
    expect(document.getElementById('download-bar').classList.contains('hidden-by-tab')).toBe(false)
  })
})


/* ====================================== M-4: Saved view renders */

function _esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]))
}

function _renderDiscoverV2Saved(rows) {
  const list = document.getElementById('disc-v2-saved-list')
  const count = document.getElementById('disc-v2-saved-count')
  if (!list) return
  if (!rows || !rows.length) {
    list.innerHTML = 'No saved releases yet. Click 💚 on any card in the feed.'
    if (count) count.textContent = ''
    return
  }
  if (count) count.textContent = `(${rows.length})`
  list.innerHTML = ''
  rows.forEach(r => {
    const row = document.createElement('div')
    const meta = document.createElement('span')
    meta.innerHTML = `<strong>${_esc(r.title || 'Untitled')}</strong> <span>${_esc(r.artist || '')}${r.label ? ' · ' + _esc(r.label) : ''}</span>`
    row.appendChild(meta)
    if (r.release_id) {
      const link = document.createElement('a')
      link.href = `https://www.discogs.com/release/${r.release_id}`
      link.target = '_blank'
      link.rel = 'noopener noreferrer'
      link.textContent = '↗'
      row.appendChild(link)
    }
    const unsave = document.createElement('button')
    unsave.textContent = 'Unsave'
    unsave.setAttribute('data-release-key', r.release_key)
    row.appendChild(unsave)
    list.appendChild(row)
  })
}


describe('_renderDiscoverV2Saved', () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <strong>Saved <span id="disc-v2-saved-count"></span></strong>
      <div id="disc-v2-saved-list"></div>
    `
  })

  it('empty-state message when no saves', () => {
    _renderDiscoverV2Saved([])
    expect(document.body.textContent).toContain('No saved releases yet')
    expect(document.getElementById('disc-v2-saved-count').textContent).toBe('')
  })

  it('renders one row per save with Discogs link', () => {
    _renderDiscoverV2Saved([
      {release_key: 'k1', release_id: 100, artist: 'A1', title: 'T1', label: 'L1'},
      {release_key: 'k2', release_id: 200, artist: 'A2', title: 'T2'},
    ])
    expect(document.getElementById('disc-v2-saved-count').textContent).toBe('(2)')
    const links = document.querySelectorAll('a[href^="https://www.discogs.com/release/"]')
    expect(links.length).toBe(2)
    expect(links[0].getAttribute('href')).toBe('https://www.discogs.com/release/100')
  })

  it('renders no Discogs link when release_id is missing', () => {
    _renderDiscoverV2Saved([{release_key: 'k1', artist: 'A', title: 'T'}])
    expect(document.querySelectorAll('a').length).toBe(0)
  })

  it('Unsave button carries the release_key', () => {
    _renderDiscoverV2Saved([{release_key: 'k42', artist: 'A', title: 'T'}])
    const btn = document.querySelector('button')
    expect(btn.textContent).toBe('Unsave')
    expect(btn.getAttribute('data-release-key')).toBe('k42')
  })

  it('escapes XSS in saved-row fields', () => {
    _renderDiscoverV2Saved([{release_key: 'k1', artist: '<img src=x>', title: '<svg>'}])
    expect(document.body.innerHTML).not.toContain('<img src=x>')
    expect(document.body.innerHTML).not.toContain('<svg>')
    expect(document.body.textContent).toContain('<img src=x>')
  })
})
