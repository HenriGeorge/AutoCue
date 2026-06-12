/**
 * P0 foundations — single-file source reconstruction for source-reading specs.
 *
 * docs/index.html is being split into docs/css/* + docs/js/* (multi-file,
 * no build step). Specs that read the app SOURCE (token extraction,
 * markup ↔ JS id contracts, user-copy audits) should call loadAppHtml()
 * instead of readFileSync(docs/index.html): it returns index.html with every
 * LOCAL stylesheet <link> and <script src> inlined at its tag position —
 * i.e. the pre-split single-file view, identical before and after the split.
 * CDN tags (http/https URLs) are left untouched.
 */
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const DOCS = resolve(__dirname, '..', '..', 'docs')

const isLocal = (url) => url && !/^https?:\/\//.test(url) && !url.startsWith('//')

export function loadAppHtml() {
  let html = readFileSync(resolve(DOCS, 'index.html'), 'utf8')

  // Inline local stylesheets: <link rel="stylesheet" href="css/app.css">
  html = html.replace(/<link\b[^>]*>/gi, (tag) => {
    if (!/rel=["']stylesheet["']/i.test(tag)) return tag
    const m = tag.match(/href=["']([^"']+)["']/i)
    if (!m || !isLocal(m[1])) return tag
    return `<style>\n${readFileSync(resolve(DOCS, m[1]), 'utf8')}\n</style>`
  })

  // Inline local scripts: <script src="js/app.js"></script> (any type)
  html = html.replace(/<script\b[^>]*\bsrc=["']([^"']+)["'][^>]*>\s*<\/script>/gi, (tag, src) => {
    if (!isLocal(src)) return tag
    return `<script>\n${readFileSync(resolve(DOCS, src), 'utf8')}\n</script>`
  })

  return html
}
