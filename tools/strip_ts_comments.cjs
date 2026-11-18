// Removes comments from TS/TSX/JS using the TypeScript parser (never touches strings, regexes or JSX text).
// stdin: source, argv[2]: path (for the file kind), stdout: source without comments.
const fs = require('fs')
const path = require('path')
const ts = require(path.join(__dirname, 'node_modules', 'typescript'))

const file = process.argv[2] || 'x.ts'
const src = fs.readFileSync(0, 'utf8')
const ext = path.extname(file).toLowerCase()
const kind = ext === '.tsx' ? ts.ScriptKind.TSX : ext === '.jsx' ? ts.ScriptKind.JSX
  : ext === '.ts' ? ts.ScriptKind.TS : ts.ScriptKind.JS
const sf = ts.createSourceFile(file, src, ts.ScriptTarget.Latest, true, kind)

const ranges = new Map()
const jsxText = []
const emptyJsxExpr = []
const add = (rs) => { if (rs) for (const r of rs) ranges.set(r.pos, r) }

function visit(n) {
  if (n.kind === ts.SyntaxKind.JsxText) { jsxText.push([n.pos, n.end]); return }
  if (n.kind === ts.SyntaxKind.JsxExpression && !n.expression) emptyJsxExpr.push([n.getStart(sf), n.end])
  add(ts.getLeadingCommentRanges(src, n.pos))
  add(ts.getTrailingCommentRanges(src, n.end))
  for (const c of n.getChildren(sf)) visit(c)
}
visit(sf)

const inside = (r, spans) => spans.some(([a, b]) => r.pos >= a && r.end <= b)
let cuts = [...ranges.values()].filter((r) => !inside(r, jsxText) && !(r.pos === 0 && src.startsWith('#!')))
for (const [a, b] of emptyJsxExpr) {
  if (cuts.some((r) => r.pos >= a && r.end <= b)) {
    cuts = cuts.filter((r) => !(r.pos >= a && r.end <= b))
    cuts.push({ pos: a, end: b, kind: ts.SyntaxKind.MultiLineCommentTrivia })
  }
}
cuts.sort((x, y) => x.pos - y.pos)

let out = ''
let at = 0
for (const r of cuts) {
  if (r.pos < at) continue
  let start = r.pos
  let end = r.end
  const lineStart = src.lastIndexOf('\n', start - 1) + 1
  let lineEnd = src.indexOf('\n', end)
  if (lineEnd === -1) lineEnd = src.length
  const before = src.slice(lineStart, start)
  const after = src.slice(end, lineEnd)
  if (/^\s*$/.test(before) && /^\s*$/.test(after) && lineStart >= at) {
    start = lineStart
    end = Math.min(lineEnd + 1, src.length)
  } else if (/^\s*$/.test(after)) {
    while (start > at && (src[start - 1] === ' ' || src[start - 1] === '\t')) start--
  }
  out += src.slice(at, start)
  at = end
}
out += src.slice(at)
process.stdout.write(out)
