/**
 * Issue #114 — album → flat sort must drop the album-mode DOM before
 * Virtualizer.attach() runs, otherwise 305 .album-group nodes (and ~3.8k
 * cached .track-card children) leak into the flat-mode container, defeating
 * the bounded-DOM goal of the Virtualizer (TASK-032 / PR #99).
 *
 * Mirrors the cleanup added at the top of the flat-mode branch of
 * `renderTrackList` in docs/index.html (`if (list.querySelector('.album-group'))
 * { list.innerHTML = ''; _cardMap.clear(); }`). Keep this test and the
 * production branch in lock-step.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';

// ── Production helper, copied verbatim from docs/index.html ───────────────
// The fix is a two-statement guard. Ported as a single helper so the test
// pins the exact invariant the production branch must satisfy: any time the
// previous render produced .album-group children, the container must be
// emptied and the album-mode card cache cleared before the Virtualizer is
// (re)attached.

function flatModeAlbumCleanup(list, cardMap) {
  if (list.querySelector('.album-group')) {
    list.innerHTML = '';
    cardMap.clear();
  }
}

function buildAlbumGroup(albumName, trackCount) {
  const group = document.createElement('div');
  group.className = 'album-group';

  const header = document.createElement('div');
  header.className = 'album-header';
  header.textContent = albumName;
  group.appendChild(header);

  const tracksDiv = document.createElement('div');
  tracksDiv.className = 'album-tracks';
  for (let i = 0; i < trackCount; i++) {
    const card = document.createElement('div');
    card.className = 'track-card';
    card.dataset.trackId = `${albumName}-${i}`;
    tracksDiv.appendChild(card);
  }
  group.appendChild(tracksDiv);
  return group;
}

function buildAlbumModeContainer({ groups = 305, tracksPerGroup = 12 } = {}) {
  const list = document.createElement('div');
  list.id = 'track-list';
  for (let g = 0; g < groups; g++) {
    list.appendChild(buildAlbumGroup(`Album ${g}`, tracksPerGroup));
  }
  return list;
}

describe('Issue #114 — album → flat sort transition cleanup', () => {
  let list;
  let cardMap;

  beforeEach(() => {
    cardMap = new Map();
    list = null;
  });

  afterEach(() => {
    if (list && list.parentNode) list.parentNode.removeChild(list);
  });

  it('REGRESSION: 305 .album-group nodes survive the transition WITHOUT the fix', () => {
    // This is the exact failure mode from the QA probe in issue #114. We
    // simulate the bug by skipping the cleanup, then assert that 305 album
    // groups (and their 3,791 cached cards) stay in the container.
    list = buildAlbumModeContainer({ groups: 305, tracksPerGroup: 12 });

    // Populate the album-mode cache the way renderTrackList does — every
    // track card is keyed by track id.
    for (const card of list.querySelectorAll('.track-card')) {
      cardMap.set(card.dataset.trackId, card);
    }

    // No cleanup runs — this is the pre-fix behaviour we're guarding against.
    // (Intentionally do NOT call flatModeAlbumCleanup here.)
    expect(list.querySelectorAll('.album-group').length).toBe(305);
    expect(list.querySelectorAll('.track-card').length).toBe(305 * 12);
    expect(cardMap.size).toBe(305 * 12);
  });

  it('clears .album-group DOM and _cardMap on the album → flat transition', () => {
    list = buildAlbumModeContainer({ groups: 305, tracksPerGroup: 12 });
    for (const card of list.querySelectorAll('.track-card')) {
      cardMap.set(card.dataset.trackId, card);
    }

    flatModeAlbumCleanup(list, cardMap);

    // After cleanup the container must be empty — Virtualizer.attach() will
    // then own all subsequent children (its spacer + the recycled window).
    expect(list.children.length).toBe(0);
    expect(list.querySelectorAll('.album-group').length).toBe(0);
    expect(list.querySelectorAll('.track-card').length).toBe(0);
    // _cardMap must be cleared so the ~3.8k now-detached nodes are eligible
    // for GC, not pinned by the cache for the rest of the session.
    expect(cardMap.size).toBe(0);
  });

  it('BOUNDARY: no-op when the container has no .album-group (flat → flat re-sort)', () => {
    // Pre-populate the container with virtualizer-shaped children — a spacer
    // plus a handful of absolutely-positioned track cards. The cleanup MUST
    // leave them alone, and MUST NOT clear _cardMap (clearing it would force
    // an unnecessary album-mode rebuild on the next album-sort.)
    list = document.createElement('div');
    list.id = 'track-list';

    const spacer = document.createElement('div');
    spacer.style.position = 'relative';
    list.appendChild(spacer);
    for (let i = 0; i < 16; i++) {
      const card = document.createElement('div');
      card.className = 'track-card';
      card.dataset.trackId = `flat-${i}`;
      list.appendChild(card);
    }

    // Seed _cardMap as if a previous album-mode render had cached entries
    // (which the flat branch doesn't use). The guard must not touch it.
    cardMap.set('previous-album-cache', document.createElement('div'));

    flatModeAlbumCleanup(list, cardMap);

    // Spacer + 16 track cards still present — cleanup left them alone.
    expect(list.children.length).toBe(17);
    expect(list.querySelectorAll('.track-card').length).toBe(16);
    // _cardMap untouched — only album-mode renders manage this cache.
    expect(cardMap.size).toBe(1);
  });

  it('INVARIANT: after cleanup, container is empty even when the album subtree is deeply nested', () => {
    // Property-style: regardless of how many groups / how deep the children
    // nest, a single .album-group anywhere in the container triggers a full
    // wipe. The guard uses querySelector (descendant scope), not children.
    list = document.createElement('div');
    list.id = 'track-list';

    const outerWrapper = document.createElement('div');
    outerWrapper.className = 'some-wrapper';
    const innerWrapper = document.createElement('div');
    innerWrapper.className = 'another-wrapper';
    innerWrapper.appendChild(buildAlbumGroup('Deeply Nested', 5));
    outerWrapper.appendChild(innerWrapper);
    list.appendChild(outerWrapper);

    for (const card of list.querySelectorAll('.track-card')) {
      cardMap.set(card.dataset.trackId, card);
    }
    expect(cardMap.size).toBe(5);

    flatModeAlbumCleanup(list, cardMap);

    expect(list.children.length).toBe(0);
    expect(list.querySelector('.album-group')).toBeNull();
    expect(cardMap.size).toBe(0);
  });
});
