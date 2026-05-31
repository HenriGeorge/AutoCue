/**
 * Tests for the XML upload and cue-generation logic in docs/index.html.
 *
 * The two functions under test are copied verbatim from the HTML file so
 * they run in the jsdom environment without a build step.  If you change
 * parseRekordboxXml or generateCues in index.html, update the copies below.
 *
 * Source locations (docs/index.html):
 *   parseRekordboxXml  — lines 775–827
 *   generateCues       — lines 830–842
 */

import { describe, it, expect } from 'vitest'

// ── Functions under test (verbatim from docs/index.html) ───────────────────

function parseRekordboxXml(xmlString) {
  const parser = new DOMParser()
  const doc = parser.parseFromString(xmlString, 'text/xml')

  if (doc.querySelector('parsererror')) {
    return { error: "This file couldn't be parsed as XML. Make sure it's a valid rekordbox.xml export." }
  }
  if (!doc.querySelector('DJ_PLAYLISTS')) {
    return { error: "This doesn't look like a Rekordbox export. In Rekordbox go to File → Export Collection in rekordbox format." }
  }

  const tracks = [...doc.querySelectorAll('COLLECTION > TRACK')].map(el => {
    const tempoEl = el.querySelector('TEMPO')
    let tempo = null
    if (tempoEl) {
      const beatsPerBar = parseInt((tempoEl.getAttribute('Metro') || '4/4').split('/')[0], 10) || 4
      tempo = {
        bpm:        parseFloat(tempoEl.getAttribute('Bpm'))    || 0,
        inizio:     parseFloat(tempoEl.getAttribute('Inizio')) || 0,
        beatsPerBar,
      }
    }

    const rawLocation = el.getAttribute('Location') || ''
    let locationFilename = ''
    try { locationFilename = decodeURIComponent(rawLocation.split('/').pop()) }
    catch { locationFilename = rawLocation.split('/').pop() }

    const existingCueDetails = [...el.querySelectorAll('POSITION_MARK')]
      .filter(pm => parseInt(pm.getAttribute('Num'), 10) >= 0)
      .map(pm => ({
        num:   parseInt(pm.getAttribute('Num'), 10),
        name:  pm.getAttribute('Name') || '',
        start: parseFloat(pm.getAttribute('Start')),
      }))
    const existingHotCues = existingCueDetails.length

    return {
      el,
      id:               el.getAttribute('TrackID'),
      name:             el.getAttribute('Name')   || '(no title)',
      artist:           el.getAttribute('Artist') || '',
      totalTime:        parseFloat(el.getAttribute('TotalTime')) || 0,
      bpm:              parseFloat(el.getAttribute('AverageBpm')) || (tempo ? tempo.bpm : 0),
      tempo,
      existingHotCues,
      existingCueDetails,
      locationFilename,
    }
  })
  return { doc, tracks }
}

function generateCues(track, barsInterval, startBar, maxCues) {
  if (!track.tempo || !track.tempo.bpm) return []
  const { bpm, inizio, beatsPerBar } = track.tempo
  const barDuration = (60.0 / bpm) * beatsPerBar
  const cues = []
  for (let i = 0; i < maxCues; i++) {
    const posSec = inizio + (startBar - 1 + i * barsInterval) * barDuration
    if (posSec < 0) continue
    if (track.totalTime > 0 && posSec >= track.totalTime) break
    const barNumber = startBar + i * barsInterval
    cues.push({ slot: i, posSec: Math.round(posSec * 1000) / 1000, name: `Bar ${barNumber}` })
  }
  return cues
}

// ── Helpers ────────────────────────────────────────────────────────────────

function minimalXml(collectionInner = '') {
  return `<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <COLLECTION Entries="0">${collectionInner}</COLLECTION>
  <PLAYLISTS><NODE Type="0" Name="ROOT" Count="0"/></PLAYLISTS>
</DJ_PLAYLISTS>`
}

function trackXml({
  id = '1',
  name = 'Test Track',
  artist = 'Test Artist',
  totalTime = '300',
  avgBpm = '128.00',
  location = 'file://localhost/Music/test.mp3',
  tempoAttrs = 'Bpm="128.00" Inizio="0.059" Metro="4/4" Battito="1"',
  noTempo = false,
  marks = '',
} = {}) {
  const tempoTag = noTempo ? '' : `<TEMPO ${tempoAttrs}/>`
  return `<TRACK TrackID="${id}" Name="${name}" Artist="${artist}"
    TotalTime="${totalTime}" AverageBpm="${avgBpm}"
    Location="${location}">
    ${tempoTag}
    ${marks}
  </TRACK>`
}

// ── parseRekordboxXml ──────────────────────────────────────────────────────

describe('parseRekordboxXml — invalid input', () => {
  it('returns error for empty string', () => {
    const r = parseRekordboxXml('')
    expect(r).toHaveProperty('error')
    expect(r.error).toMatch(/parsed as XML/i)
  })

  it('returns error for plain text', () => {
    const r = parseRekordboxXml('this is not xml at all <<<')
    expect(r).toHaveProperty('error')
  })

  it('returns error for wrong XML root element', () => {
    const r = parseRekordboxXml('<RSS><channel/></RSS>')
    expect(r).toHaveProperty('error')
    expect(r.error).toMatch(/rekordbox/i)
  })

  it('does not have error when DJ_PLAYLISTS root is present', () => {
    const r = parseRekordboxXml(minimalXml())
    expect(r).not.toHaveProperty('error')
  })
})

describe('parseRekordboxXml — empty collection', () => {
  it('returns empty tracks array', () => {
    const { tracks } = parseRekordboxXml(minimalXml())
    expect(tracks).toEqual([])
  })

  it('returns a doc object', () => {
    const { doc } = parseRekordboxXml(minimalXml())
    expect(doc).toBeTruthy()
  })
})

describe('parseRekordboxXml — track fields', () => {
  it('extracts TrackID', () => {
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ id: '42' })))
    expect(tracks[0].id).toBe('42')
  })

  it('extracts Name', () => {
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ name: 'My Song' })))
    expect(tracks[0].name).toBe('My Song')
  })

  it('defaults name to "(no title)" when Name attribute missing', () => {
    const xml = minimalXml(`<TRACK TrackID="1" Artist="A" TotalTime="200"><TEMPO Bpm="120" Inizio="0" Metro="4/4" Battito="1"/></TRACK>`)
    const { tracks } = parseRekordboxXml(xml)
    expect(tracks[0].name).toBe('(no title)')
  })

  it('extracts Artist', () => {
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ artist: 'DJ Example' })))
    expect(tracks[0].artist).toBe('DJ Example')
  })

  it('defaults artist to "" when Artist attribute missing', () => {
    const xml = minimalXml(`<TRACK TrackID="1" Name="Track" TotalTime="200"><TEMPO Bpm="120" Inizio="0" Metro="4/4" Battito="1"/></TRACK>`)
    const { tracks } = parseRekordboxXml(xml)
    expect(tracks[0].artist).toBe('')
  })

  it('extracts TotalTime as number', () => {
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ totalTime: '245.5' })))
    expect(tracks[0].totalTime).toBeCloseTo(245.5)
  })

  it('defaults totalTime to 0 when attribute missing', () => {
    const xml = minimalXml(`<TRACK TrackID="1" Name="T"><TEMPO Bpm="120" Inizio="0" Metro="4/4" Battito="1"/></TRACK>`)
    const { tracks } = parseRekordboxXml(xml)
    expect(tracks[0].totalTime).toBe(0)
  })

  it('extracts multiple tracks', () => {
    const xml = minimalXml(trackXml({ id: '1' }) + trackXml({ id: '2' }))
    const { tracks } = parseRekordboxXml(xml)
    expect(tracks).toHaveLength(2)
    expect(tracks[0].id).toBe('1')
    expect(tracks[1].id).toBe('2')
  })
})

describe('parseRekordboxXml — TEMPO element', () => {
  it('sets tempo to null when TEMPO element is missing', () => {
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ noTempo: true })))
    expect(tracks[0].tempo).toBeNull()
  })

  it('extracts BPM', () => {
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ tempoAttrs: 'Bpm="174.00" Inizio="0.5" Metro="4/4" Battito="1"' })))
    expect(tracks[0].tempo.bpm).toBeCloseTo(174)
  })

  it('extracts Inizio', () => {
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ tempoAttrs: 'Bpm="128.00" Inizio="0.059" Metro="4/4" Battito="1"' })))
    expect(tracks[0].tempo.inizio).toBeCloseTo(0.059)
  })

  it('defaults inizio to 0 when Inizio attribute missing', () => {
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ tempoAttrs: 'Bpm="128.00" Metro="4/4" Battito="1"' })))
    expect(tracks[0].tempo.inizio).toBe(0)
  })

  it('parses 4/4 Metro as beatsPerBar=4', () => {
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ tempoAttrs: 'Bpm="120" Inizio="0" Metro="4/4" Battito="1"' })))
    expect(tracks[0].tempo.beatsPerBar).toBe(4)
  })

  it('parses 3/4 Metro as beatsPerBar=3', () => {
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ tempoAttrs: 'Bpm="120" Inizio="0" Metro="3/4" Battito="1"' })))
    expect(tracks[0].tempo.beatsPerBar).toBe(3)
  })

  it('defaults beatsPerBar to 4 when Metro missing', () => {
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ tempoAttrs: 'Bpm="120" Inizio="0"' })))
    expect(tracks[0].tempo.beatsPerBar).toBe(4)
  })

  it('stores Bpm=0 as bpm=0 (unanalyzed track)', () => {
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ avgBpm: '0', tempoAttrs: 'Bpm="0" Inizio="0" Metro="4/4" Battito="1"' })))
    expect(tracks[0].tempo.bpm).toBe(0)
  })
})

describe('parseRekordboxXml — Location filename decoding', () => {
  it('extracts filename from Location URL', () => {
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ location: 'file://localhost/Music/my_track.mp3' })))
    expect(tracks[0].locationFilename).toBe('my_track.mp3')
  })

  it('decodes percent-encoded spaces in filename', () => {
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ location: 'file://localhost/Music/my%20track.mp3' })))
    expect(tracks[0].locationFilename).toBe('my track.mp3')
  })

  it('decodes percent-encoded special characters', () => {
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ location: 'file://localhost/Music/track%20(remix).mp3' })))
    expect(tracks[0].locationFilename).toBe('track (remix).mp3')
  })

  it('falls back to raw filename on malformed percent encoding', () => {
    // %ZZ is invalid percent encoding — should not throw
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ location: 'file://localhost/Music/bad%ZZname.mp3' })))
    expect(tracks[0].locationFilename).toBe('bad%ZZname.mp3')
  })

  it('returns empty string when Location attribute missing', () => {
    const xml = minimalXml(`<TRACK TrackID="1" Name="T" TotalTime="200"><TEMPO Bpm="120" Inizio="0" Metro="4/4" Battito="1"/></TRACK>`)
    const { tracks } = parseRekordboxXml(xml)
    expect(tracks[0].locationFilename).toBe('')
  })
})

describe('parseRekordboxXml — existing cues', () => {
  it('counts a hot cue (Num>=0) as existingHotCues=1', () => {
    const marks = '<POSITION_MARK Name="Intro" Type="0" Start="0.059" Num="0" Red="40" Green="226" Blue="20"/>'
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ marks })))
    expect(tracks[0].existingHotCues).toBe(1)
  })

  it('does not count memory cue (Num=-1) in existingHotCues', () => {
    const marks = '<POSITION_MARK Name="Mem" Type="0" Start="10.0" Num="-1" Red="0" Green="0" Blue="0"/>'
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ marks })))
    expect(tracks[0].existingHotCues).toBe(0)
  })

  it('counts only hot cues when both hot and memory cues present', () => {
    const marks = `
      <POSITION_MARK Name="HotA" Type="0" Start="5.0" Num="0" Red="40" Green="226" Blue="20"/>
      <POSITION_MARK Name="Mem" Type="0" Start="10.0" Num="-1" Red="0" Green="0" Blue="0"/>
    `
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ marks })))
    expect(tracks[0].existingHotCues).toBe(1)
  })

  it('counts multiple hot cues correctly', () => {
    const marks = `
      <POSITION_MARK Name="A" Type="0" Start="1.0" Num="0"/>
      <POSITION_MARK Name="B" Type="0" Start="2.0" Num="1"/>
      <POSITION_MARK Name="C" Type="0" Start="3.0" Num="2"/>
    `
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ marks })))
    expect(tracks[0].existingHotCues).toBe(3)
  })

  it('populates existingCueDetails with num/name/start', () => {
    const marks = '<POSITION_MARK Name="Intro" Type="0" Start="5.5" Num="2" Red="0" Green="224" Blue="255"/>'
    const { tracks } = parseRekordboxXml(minimalXml(trackXml({ marks })))
    expect(tracks[0].existingCueDetails).toHaveLength(1)
    expect(tracks[0].existingCueDetails[0]).toMatchObject({ num: 2, name: 'Intro', start: 5.5 })
  })
})

// ── generateCues ───────────────────────────────────────────────────────────

function makeTrack({ bpm = 128, inizio = 0, beatsPerBar = 4, totalTime = 300 } = {}) {
  return { tempo: { bpm, inizio, beatsPerBar }, totalTime }
}

describe('generateCues — early returns', () => {
  it('returns [] when tempo is null', () => {
    expect(generateCues({ tempo: null, totalTime: 300 }, 16, 1, 8)).toEqual([])
  })

  it('returns [] when bpm is 0', () => {
    expect(generateCues(makeTrack({ bpm: 0 }), 16, 1, 8)).toEqual([])
  })
})

describe('generateCues — normal 128 BPM, 4/4', () => {
  // barDuration = (60/128) * 4 = 1.875s
  // cue[i] = 0 + i*16 * 1.875

  it('returns exactly maxCues cues when track is long enough', () => {
    const cues = generateCues(makeTrack(), 16, 1, 8)
    expect(cues).toHaveLength(8)
  })

  it('first cue is at inizio (bar 1, i=0)', () => {
    const cues = generateCues(makeTrack({ inizio: 0.059 }), 16, 1, 8)
    expect(cues[0].posSec).toBeCloseTo(0.059, 2)
  })

  it('second cue is 16 bars after first', () => {
    const cues = generateCues(makeTrack({ inizio: 0 }), 16, 1, 8)
    const barDuration = (60 / 128) * 4  // 1.875
    expect(cues[1].posSec).toBeCloseTo(16 * barDuration, 3)
  })

  it('slot numbers start at 0 for first cue', () => {
    const cues = generateCues(makeTrack(), 16, 1, 8)
    expect(cues[0].slot).toBe(0)
  })

  it('slot numbers are consecutive', () => {
    const cues = generateCues(makeTrack(), 16, 1, 8)
    cues.forEach((c, i) => expect(c.slot).toBe(i))
  })

  it('positions are rounded to 3 decimal places', () => {
    const cues = generateCues(makeTrack({ bpm: 120, inizio: 0 }), 7, 1, 4)
    cues.forEach(c => {
      const str = String(c.posSec)
      const decimals = str.includes('.') ? str.split('.')[1].length : 0
      expect(decimals).toBeLessThanOrEqual(3)
    })
  })
})

describe('generateCues — maxCues limit', () => {
  it('returns at most maxCues cues', () => {
    expect(generateCues(makeTrack(), 16, 1, 3)).toHaveLength(3)
  })

  it('returns 8 with maxCues=8', () => {
    expect(generateCues(makeTrack(), 16, 1, 8)).toHaveLength(8)
  })
})

describe('generateCues — startBar offset', () => {
  it('startBar=1 starts at inizio', () => {
    const cues = generateCues(makeTrack({ inizio: 0 }), 16, 1, 1)
    expect(cues[0].posSec).toBeCloseTo(0)
  })

  it('startBar=5 skips first 4 bars', () => {
    const barDuration = (60 / 128) * 4
    const cues = generateCues(makeTrack({ inizio: 0 }), 16, 5, 1)
    expect(cues[0].posSec).toBeCloseTo(4 * barDuration, 3)
  })
})

describe('generateCues — barsInterval', () => {
  it('barsInterval=1 places cues every bar', () => {
    const barDuration = (60 / 128) * 4
    const cues = generateCues(makeTrack({ inizio: 0 }), 1, 1, 3)
    expect(cues[0].posSec).toBeCloseTo(0)
    expect(cues[1].posSec).toBeCloseTo(barDuration, 3)
    expect(cues[2].posSec).toBeCloseTo(2 * barDuration, 3)
  })

  it('barsInterval=32 spaces cues 32 bars apart', () => {
    const barDuration = (60 / 128) * 4
    const cues = generateCues(makeTrack({ totalTime: 999 }), 32, 1, 2)
    expect(cues[1].posSec - cues[0].posSec).toBeCloseTo(32 * barDuration, 3)
  })
})

describe('generateCues — totalTime boundary', () => {
  it('stops generating when posSec >= totalTime', () => {
    // 128 BPM, 16-bar interval: cue[1] = 30s. Set totalTime=25 → only 1 cue
    const cues = generateCues(makeTrack({ totalTime: 25 }), 16, 1, 8)
    expect(cues).toHaveLength(1)
  })

  it('generates all maxCues when totalTime=0 (missing in XML)', () => {
    const cues = generateCues(makeTrack({ totalTime: 0 }), 16, 1, 8)
    expect(cues).toHaveLength(8)
  })
})

describe('generateCues — negative inizio', () => {
  it('skips cues with negative positions', () => {
    // inizio=-3.75, barsInterval=1: first 2 bars negative, rest positive
    const barDuration = (60 / 128) * 4  // 1.875
    const cues = generateCues(makeTrack({ inizio: -3.75, totalTime: 999 }), 1, 1, 8)
    cues.forEach(c => expect(c.posSec).toBeGreaterThanOrEqual(0))
  })

  it('slot numbers reflect original loop index even with skipped negatives', () => {
    // inizio=-3.75, barsInterval=1: i=0 (pos=-3.75, skip), i=1 (pos=-1.875, skip), i=2 (pos=0)
    const cues = generateCues(makeTrack({ inizio: -3.75, totalTime: 999 }), 1, 1, 4)
    expect(cues[0].slot).toBe(2)
  })
})

describe('generateCues — 3/4 time', () => {
  it('uses beatsPerBar=3 for bar duration', () => {
    const barDuration3_4 = (60 / 120) * 3  // 1.5s per bar at 120 BPM
    const barDuration4_4 = (60 / 120) * 4  // 2.0s per bar at 120 BPM
    const cues3 = generateCues(makeTrack({ bpm: 120, inizio: 0, beatsPerBar: 3 }), 1, 1, 2)
    const cues4 = generateCues(makeTrack({ bpm: 120, inizio: 0, beatsPerBar: 4 }), 1, 1, 2)
    expect(cues3[1].posSec).toBeCloseTo(barDuration3_4, 3)
    expect(cues4[1].posSec).toBeCloseTo(barDuration4_4, 3)
    expect(cues3[1].posSec).not.toBeCloseTo(cues4[1].posSec)
  })
})

// ── Cue colour selection (verbatim constants from docs/index.html) ──────────

const CUE_COLORS = [
  { r: 40,  g: 226, b: 20  },
  { r: 48,  g: 90,  b: 255 },
  { r: 0,   g: 224, b: 255 },
  { r: 255, g: 160, b: 0   },
  { r: 255, g: 100, b: 0   },
  { r: 224, g: 48,  b: 30  },
  { r: 245, g: 30,  b: 140 },
  { r: 230, g: 0,   b: 255 },
]

const PHRASE_COLORS = {
  'Intro':   { r: 40,  g: 226, b: 20  },
  'Verse':   { r: 48,  g: 90,  b: 255 },
  'Bridge':  { r: 0,   g: 224, b: 255 },
  'Chorus':  { r: 255, g: 50,  b: 50  },
  'Outro':   { r: 255, g: 160, b: 0   },
  'Up':      { r: 245, g: 30,  b: 140 },
  'Down':    { r: 230, g: 0,   b: 255 },
  '?':       { r: 180, g: 180, b: 180 },
}

function pickCueColor(cue) {
  if (cue.isPhrase && PHRASE_COLORS[cue.label]) return PHRASE_COLORS[cue.label]
  const safeSlot = Math.max(0, Math.min(7, cue.slot || 0))
  return CUE_COLORS[safeSlot]
}

describe('cue colour selection', () => {
  it('phrase Chorus cue gets Chorus color (r=255,g=50,b=50), not slot-0 color', () => {
    const c = pickCueColor({ isPhrase: true, label: 'Chorus', slot: 0 })
    expect(c).toEqual({ r: 255, g: 50, b: 50 })
    expect(c).not.toEqual(CUE_COLORS[0])
  })

  it('phrase Intro cue gets Intro color (g=226)', () => {
    const c = pickCueColor({ isPhrase: true, label: 'Intro', slot: 0 })
    expect(c).toEqual({ r: 40, g: 226, b: 20 })
  })

  it('phrase Outro cue gets Outro color (r=255,g=160)', () => {
    const c = pickCueColor({ isPhrase: true, label: 'Outro', slot: 1 })
    expect(c).toEqual({ r: 255, g: 160, b: 0 })
    expect(c).not.toEqual(CUE_COLORS[1])
  })

  it('phrase unknown label (?) gets grey color', () => {
    const c = pickCueColor({ isPhrase: true, label: '?', slot: 0 })
    expect(c).toEqual({ r: 180, g: 180, b: 180 })
  })

  it('bar cue (isPhrase=false) uses CUE_COLORS slot index', () => {
    const c = pickCueColor({ isPhrase: false, label: 'Bar 1', slot: 2 })
    expect(c).toEqual(CUE_COLORS[2])
  })

  it('bar cue slot 0 uses CUE_COLORS[0] (green)', () => {
    const c = pickCueColor({ isPhrase: false, label: 'Bar 1', slot: 0 })
    expect(c).toEqual(CUE_COLORS[0])
  })

  it('phrase cue with label not in PHRASE_COLORS falls back to slot color', () => {
    const c = pickCueColor({ isPhrase: true, label: 'UnknownPhrase', slot: 3 })
    expect(c).toEqual(CUE_COLORS[3])
  })

  it('slot >= 8 clamps to CUE_COLORS[7] instead of returning undefined', () => {
    const c = pickCueColor({ isPhrase: false, label: '?', slot: 16 })
    expect(c).toBeDefined()
    expect(c).toEqual(CUE_COLORS[7])
  })

  it('slot < 0 clamps to CUE_COLORS[0]', () => {
    const c = pickCueColor({ isPhrase: false, label: '?', slot: -1 })
    expect(c).toBeDefined()
    expect(c).toEqual(CUE_COLORS[0])
  })

  it('undefined slot clamps to CUE_COLORS[0]', () => {
    const c = pickCueColor({ isPhrase: false, label: '?' })
    expect(c).toBeDefined()
    expect(c).toEqual(CUE_COLORS[0])
  })
})

describe('generateCues — cue names', () => {
  it('first cue has name "Bar 1" with default startBar=1', () => {
    const cues = generateCues(makeTrack({ inizio: 0 }), 16, 1, 2)
    expect(cues[0].name).toBe('Bar 1')
  })

  it('second cue has name "Bar 17" with barsInterval=16', () => {
    const cues = generateCues(makeTrack({ inizio: 0 }), 16, 1, 2)
    expect(cues[1].name).toBe('Bar 17')
  })

  it('name reflects startBar offset', () => {
    const cues = generateCues(makeTrack({ inizio: 0 }), 16, 5, 1)
    expect(cues[0].name).toBe('Bar 5')
  })

  it('name reflects barsInterval', () => {
    const cues = generateCues(makeTrack({ inizio: 0, totalTime: 999 }), 8, 1, 2)
    expect(cues[1].name).toBe('Bar 9')
  })
})
