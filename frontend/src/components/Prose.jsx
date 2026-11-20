import { Fragment } from 'react'

// Text written by a model or a scanner, shown as it was meant to read: paragraphs, bullet and numbered lists, **bold**
// and `code`. It is turned into elements, never into HTML, so nothing in it can reach the page as markup. A marker that
// is never closed is shown as it is.

function inline(text, key = 'i') {
  const out = []
  let plain = ''
  let i = 0
  const flush = () => { if (plain) { out.push(plain); plain = '' } }
  while (i < text.length) {
    if (text.startsWith('**', i)) {
      const end = text.indexOf('**', i + 2)
      if (end > i + 2) {
        flush()
        out.push(<strong key={`${key}b${i}`}>{inline(text.slice(i + 2, end), `${key}b${i}`)}</strong>)
        i = end + 2
        continue
      }
    }
    if (text[i] === '`') {
      const end = text.indexOf('`', i + 1)
      if (end > i + 1) {
        flush()
        out.push(<code key={`${key}c${i}`}>{text.slice(i + 1, end)}</code>)
        i = end + 1
        continue
      }
    }
    plain += text[i]
    i += 1
  }
  flush()
  return out
}

const bullet = (line) => {
  const t = line.trimStart()
  return (t.startsWith('- ') || t.startsWith('* ') || t.startsWith('• ')) ? t.slice(2) : null
}

const numbered = (line) => {
  const t = line.trimStart()
  let n = 0
  while (n < t.length && t[n] >= '0' && t[n] <= '9') n += 1
  return n > 0 && n < 4 && (t[n] === '.' || t[n] === ')') && t[n + 1] === ' ' ? t.slice(n + 2) : null
}

/** Blocks of the text: paragraphs (lines kept as line breaks) and lists. */
function blocks(text) {
  const out = []
  for (const raw of String(text || '').replaceAll('\r\n', '\n').split('\n')) {
    const line = raw.trimEnd()
    const last = out[out.length - 1]
    const item = bullet(line)
    const step = item === null ? numbered(line) : null
    if (!line.trim()) out.push({ kind: 'gap' })
    else if (item !== null) {
      if (last?.kind === 'ul') last.items.push(item)
      else out.push({ kind: 'ul', items: [item] })
    } else if (step !== null) {
      if (last?.kind === 'ol') last.items.push(step)
      else out.push({ kind: 'ol', items: [step] })
    } else if (last?.kind === 'p') last.lines.push(line)
    else out.push({ kind: 'p', lines: [line] })
  }
  return out.filter((b) => b.kind !== 'gap')
}

export default function Prose({ text, className = '' }) {
  return (
    <div className={`prose ${className}`}>
      {blocks(text).map((b, n) => {
        if (b.kind === 'ul') return <ul key={n}>{b.items.map((t, i) => <li key={i}>{inline(t, `${n}-${i}`)}</li>)}</ul>
        if (b.kind === 'ol') return <ol key={n}>{b.items.map((t, i) => <li key={i}>{inline(t, `${n}-${i}`)}</li>)}</ol>
        return <p key={n}>{b.lines.map((l, i) => <Fragment key={i}>{i > 0 && <br />}{inline(l, `${n}-${i}`)}</Fragment>)}</p>
      })}
    </div>
  )
}
