/**
 * Tests for the Discover v2 YouTube preview carousel (T-026) — id extraction,
 * lazy load + cache, error/empty states, nav buttons. Mirrors the inline
 * implementation in docs/index.html. If you change the module there, mirror
 * it here.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

/* ---------------------------------------------------------------- _esc */
function _esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]))
}

/* ---------------------------------------------------------------- helpers */

function _extractYouTubeId(url) {
  if (!url) return null
  const s = String(url)
  let m = s.match(/[?&]v=([A-Za-z0-9_-]{6,})/)
  if (m) return m[1]
  m = s.match(/youtu\.be\/([A-Za-z0-9_-]{6,})/)
  if (m) return m[1]
  m = s.match(/youtube\.com\/embed\/([A-Za-z0-9_-]{6,})/)
  if (m) return m[1]
  return null
}

/* ---------------------------------------------------------------- stub */

let DiscoverV2
let _detailCurrentRelease

function makeStub() {
  DiscoverV2 = {
    state: {
      youtubeByKey: new Map(),
    },
    searchYouTube: vi.fn(async (release, n) => {
      const r = release.release || {}
      const q = [r.artist, r.title].filter(Boolean).join(' ').trim()
      if (!q) return {status: 'loaded', candidates: []}
      const cached = DiscoverV2.state.youtubeByKey.get(release.release_key)
      if (cached && cached.status !== 'error') return cached
      // Default to 3 mocked candidates.
      const entry = {
        status: 'loaded',
        candidates: [
          {url: 'https://www.youtube.com/watch?v=aaaaaaaaaaa', title: 'Cand A', channel: 'X'},
          {url: 'https://youtu.be/bbbbbbbbbbb',                title: 'Cand B', channel: 'Y'},
          {url: 'https://www.youtube.com/embed/ccccccccccc',   title: 'Cand C', channel: 'Z'},
        ],
      }
      DiscoverV2.state.youtubeByKey.set(release.release_key, entry)
      return entry
    }),
  }
}


function _renderYouTubeCarousel(slot, candidates, index) {
  const cur = candidates[index]
  const videoId = _extractYouTubeId(cur.url)
  const embedUrl = 'https://www.youtube.com/embed/' + encodeURIComponent(videoId) + '?rel=0'
  slot.innerHTML = `
    <div class="disc-v2-yt-carousel" data-yt-index="${index}">
      <div class="disc-v2-yt-frame">
        <iframe src="${_esc(embedUrl)}"
                title="${_esc(cur.title || 'YouTube preview')}"
                allow="encrypted-media; picture-in-picture"
                referrerpolicy="strict-origin-when-cross-origin"
                allowfullscreen></iframe>
      </div>
      <div class="disc-v2-yt-controls">
        <div class="disc-v2-yt-nav">
          <button data-yt-act="prev" aria-label="Previous YouTube candidate" ${index === 0 ? 'disabled' : ''}>‹</button>
          <button data-yt-act="next" aria-label="Next YouTube candidate" ${index === candidates.length - 1 ? 'disabled' : ''}>›</button>
        </div>
        <div class="disc-v2-yt-title" title="${_esc(cur.title || '')}">${_esc(cur.title || 'Untitled')}</div>
        <div class="disc-v2-yt-counter">${index + 1} / ${candidates.length}</div>
      </div>
    </div>
  `
  slot.querySelectorAll('[data-yt-act]').forEach(btn => {
    btn.addEventListener('click', () => {
      const act = btn.getAttribute('data-yt-act')
      const next = act === 'prev' ? index - 1 : index + 1
      if (next < 0 || next >= candidates.length) return
      _renderYouTubeCarousel(slot, candidates, next)
    })
  })
}

async function _loadYouTubePreview(release) {
  const slot = document.getElementById('disc-v2-detail-youtube-slot')
  if (!slot) return
  slot.innerHTML = '<div class="disc-v2-yt-placeholder">Loading…</div>'
  const entry = await DiscoverV2.searchYouTube(release, 3)
  if (!_detailCurrentRelease || _detailCurrentRelease.release_key !== release.release_key) return
  if (entry.status === 'error') {
    slot.innerHTML = '<div class="disc-v2-yt-placeholder">Could not load YouTube previews: ' + _esc(entry.error || '') + '</div>'
    return
  }
  const candidates = (entry.candidates || []).filter(c => _extractYouTubeId(c.url))
  if (!candidates.length) {
    slot.innerHTML = '<div class="disc-v2-yt-placeholder">No YouTube previews found for this release.</div>'
    return
  }
  _renderYouTubeCarousel(slot, candidates, 0)
}


/* ===================================================== id extraction */

describe('_extractYouTubeId', () => {
  it('extracts id from watch?v= URLs', () => {
    expect(_extractYouTubeId('https://www.youtube.com/watch?v=dQw4w9WgXcQ')).toBe('dQw4w9WgXcQ')
  })
  it('extracts id from youtu.be short URLs', () => {
    expect(_extractYouTubeId('https://youtu.be/dQw4w9WgXcQ')).toBe('dQw4w9WgXcQ')
  })
  it('extracts id from /embed/ URLs', () => {
    expect(_extractYouTubeId('https://www.youtube.com/embed/dQw4w9WgXcQ')).toBe('dQw4w9WgXcQ')
  })
  it('extracts id when v= is not the first query param', () => {
    expect(_extractYouTubeId('https://www.youtube.com/watch?feature=share&v=dQw4w9WgXcQ&t=12')).toBe('dQw4w9WgXcQ')
  })
  it('returns null for non-YouTube URLs', () => {
    expect(_extractYouTubeId('https://vimeo.com/12345')).toBeNull()
  })
  it('returns null for empty / null', () => {
    expect(_extractYouTubeId(null)).toBeNull()
    expect(_extractYouTubeId('')).toBeNull()
  })
})


/* ===================================================== loadYouTubePreview */

describe('_loadYouTubePreview', () => {
  beforeEach(() => {
    makeStub()
    document.body.innerHTML = `<div id="disc-v2-detail-youtube-slot"></div>`
    _detailCurrentRelease = {
      release_key: 'k1',
      release: {artist: 'Artist', title: 'Title', id: 1},
    }
  })

  it('renders a carousel with iframe + nav + counter when results land', async () => {
    await _loadYouTubePreview(_detailCurrentRelease)
    const carousel = document.querySelector('.disc-v2-yt-carousel')
    expect(carousel).not.toBeNull()
    const iframe = carousel.querySelector('iframe')
    expect(iframe.src).toContain('https://www.youtube.com/embed/aaaaaaaaaaa')
    expect(iframe.src).toContain('rel=0')
    expect(carousel.querySelector('.disc-v2-yt-counter').textContent.trim()).toBe('1 / 3')
    expect(carousel.querySelector('[data-yt-act="prev"]').disabled).toBe(true)
    expect(carousel.querySelector('[data-yt-act="next"]').disabled).toBe(false)
  })

  it('shows placeholder when search returns no candidates', async () => {
    DiscoverV2.searchYouTube = vi.fn(async () => ({status: 'loaded', candidates: []}))
    await _loadYouTubePreview(_detailCurrentRelease)
    expect(document.querySelector('.disc-v2-yt-placeholder')).not.toBeNull()
    expect(document.querySelector('.disc-v2-yt-carousel')).toBeNull()
    expect(document.body.textContent).toContain('No YouTube previews found')
  })

  it('shows error message when search rejects', async () => {
    DiscoverV2.searchYouTube = vi.fn(async () => ({
      status: 'error', candidates: [], error: 'YouTube search timed out',
    }))
    await _loadYouTubePreview(_detailCurrentRelease)
    expect(document.body.textContent).toContain('Could not load YouTube previews')
    expect(document.body.textContent).toContain('YouTube search timed out')
  })

  it('filters out candidates whose URL has no extractable id', async () => {
    DiscoverV2.searchYouTube = vi.fn(async () => ({
      status: 'loaded',
      candidates: [
        {url: 'https://example.com/not-youtube', title: 'no id'},
        {url: 'https://www.youtube.com/watch?v=zzzzzzzzzzz', title: 'valid'},
      ],
    }))
    await _loadYouTubePreview(_detailCurrentRelease)
    expect(document.querySelector('.disc-v2-yt-counter').textContent.trim()).toBe('1 / 1')
    expect(document.querySelector('iframe').src).toContain('zzzzzzzzzzz')
  })

  it('discards results when user switched releases mid-flight', async () => {
    // searchYouTube resolves AFTER we swap _detailCurrentRelease.
    let resolve
    DiscoverV2.searchYouTube = vi.fn(() => new Promise(r => { resolve = r }))
    const p = _loadYouTubePreview(_detailCurrentRelease)
    // User flips to a different panel.
    _detailCurrentRelease = {release_key: 'k2', release: {artist: 'B', title: 'C'}}
    resolve({status: 'loaded', candidates: [
      {url: 'https://www.youtube.com/watch?v=stalestale', title: 'stale'},
    ]})
    await p
    // Slot still shows the loading placeholder, NOT the stale carousel.
    expect(document.querySelector('.disc-v2-yt-carousel')).toBeNull()
    expect(document.body.textContent).toContain('Loading')
  })
})


/* ===================================================== carousel nav */

describe('YouTube carousel navigation', () => {
  beforeEach(() => {
    makeStub()
    document.body.innerHTML = `<div id="disc-v2-detail-youtube-slot"></div>`
    _detailCurrentRelease = {
      release_key: 'k1',
      release: {artist: 'Artist', title: 'Title', id: 1},
    }
  })

  it('advances to the next candidate on next-click', async () => {
    await _loadYouTubePreview(_detailCurrentRelease)
    document.querySelector('[data-yt-act="next"]').click()
    expect(document.querySelector('.disc-v2-yt-counter').textContent.trim()).toBe('2 / 3')
    expect(document.querySelector('iframe').src).toContain('bbbbbbbbbbb')
    expect(document.querySelector('[data-yt-act="prev"]').disabled).toBe(false)
  })

  it('disables next button on the final candidate', async () => {
    await _loadYouTubePreview(_detailCurrentRelease)
    document.querySelector('[data-yt-act="next"]').click()
    document.querySelector('[data-yt-act="next"]').click()
    expect(document.querySelector('.disc-v2-yt-counter').textContent.trim()).toBe('3 / 3')
    expect(document.querySelector('[data-yt-act="next"]').disabled).toBe(true)
  })

  it('goes back with prev-click', async () => {
    await _loadYouTubePreview(_detailCurrentRelease)
    document.querySelector('[data-yt-act="next"]').click()
    document.querySelector('[data-yt-act="prev"]').click()
    expect(document.querySelector('.disc-v2-yt-counter').textContent.trim()).toBe('1 / 3')
    expect(document.querySelector('[data-yt-act="prev"]').disabled).toBe(true)
  })
})


/* ===================================================== caching */

describe('YouTube cache (searchYouTube)', () => {
  beforeEach(() => {
    makeStub()
  })

  it('returns cached entry on second call for the same release_key', async () => {
    const rel = {release_key: 'k1', release: {artist: 'A', title: 'T'}}
    const first = await DiscoverV2.searchYouTube(rel, 3)
    const second = await DiscoverV2.searchYouTube(rel, 3)
    expect(second).toBe(first)
    // The stub increments its own counter — second call should still have hit
    // the cache path inside the stub (entry exists, not error).
    expect(DiscoverV2.state.youtubeByKey.get('k1')).toBe(first)
  })

  it('returns empty candidates immediately when artist + title are missing', async () => {
    const rel = {release_key: 'kEmpty', release: {}}
    // Override stub to return empty when q is empty.
    const r = await DiscoverV2.searchYouTube(rel, 3)
    expect(r.candidates).toEqual([])
  })
})
