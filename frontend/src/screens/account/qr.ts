// Small self-contained QR Code encoder (byte mode, versions 1–10, error correction M then L).
// Enough for an otpauth:// link; follows ISO/IEC 18004 (structure after Project Nayuki's reference encoder).

type Ecl = 'M' | 'L'
const ECC_PER_BLOCK: Record<Ecl, number[]> = {
  L: [0, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18],
  M: [0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26],
}
const NUM_BLOCKS: Record<Ecl, number[]> = {
  L: [0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4],
  M: [0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5],
}
const FORMAT_BITS: Record<Ecl, number> = { L: 1, M: 0 }
const MAX_VERSION = 10

function rawModules(ver: number): number {
  let n = (16 * ver + 128) * ver + 64
  if (ver >= 2) {
    const a = Math.floor(ver / 7) + 2
    n -= (25 * a - 10) * a - 55
    if (ver >= 7) n -= 36
  }
  return n
}
const dataCodewords = (ver: number, ecl: Ecl) => Math.floor(rawModules(ver) / 8) - ECC_PER_BLOCK[ecl][ver] * NUM_BLOCKS[ecl][ver]

// ---- Reed–Solomon over GF(2^8 / 0x11D)
function mul(x: number, y: number): number {
  let z = 0
  for (let i = 7; i >= 0; i--) { z = (z << 1) ^ ((z >>> 7) * 0x11d); z ^= ((y >>> i) & 1) * x }
  return z & 0xff
}
function rsDivisor(degree: number): number[] {
  const r = new Array<number>(degree).fill(0)
  r[degree - 1] = 1
  let root = 1
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < degree; j++) { r[j] = mul(r[j], root); if (j + 1 < degree) r[j] ^= r[j + 1] }
    root = mul(root, 2)
  }
  return r
}
function rsRemainder(data: number[], divisor: number[]): number[] {
  const r = new Array<number>(divisor.length).fill(0)
  for (const b of data) {
    const f = b ^ (r.shift() as number)
    r.push(0)
    divisor.forEach((c, i) => { r[i] ^= mul(c, f) })
  }
  return r
}

function interleave(data: number[], ver: number, ecl: Ecl): number[] {
  const blocks = NUM_BLOCKS[ecl][ver], eccLen = ECC_PER_BLOCK[ecl][ver]
  const raw = Math.floor(rawModules(ver) / 8)
  const shortBlocks = blocks - (raw % blocks)
  const shortLen = Math.floor(raw / blocks)
  const div = rsDivisor(eccLen)
  const out: number[][] = []
  for (let i = 0, k = 0; i < blocks; i++) {
    const dat = data.slice(k, k + shortLen - eccLen + (i < shortBlocks ? 0 : 1))
    k += dat.length
    const ecc = rsRemainder(dat, div)
    if (i < shortBlocks) dat.push(0)
    out.push(dat.concat(ecc))
  }
  const res: number[] = []
  for (let i = 0; i < out[0].length; i++) {
    out.forEach((b, j) => { if (i !== shortLen - eccLen || j >= shortBlocks) res.push(b[i]) })
  }
  return res
}

function alignPositions(ver: number): number[] {
  if (ver === 1) return []
  const n = Math.floor(ver / 7) + 2
  const step = Math.ceil((ver * 4 + 4) / (n * 2 - 2)) * 2
  const res = [6]
  for (let pos = ver * 4 + 17 - 7; res.length < n; pos -= step) res.splice(1, 0, pos)
  return res
}

/** Returns the module matrix (true = dark), without a quiet zone; null when the text does not fit. */
export function qrMatrix(text: string): boolean[][] | null {
  const bytes = Array.from(new TextEncoder().encode(text))
  let ver = 0, ecl: Ecl = 'M'
  pick: for (const e of ['M', 'L'] as Ecl[]) {
    for (let v = 1; v <= MAX_VERSION; v++) {
      const need = 4 + (v < 10 ? 8 : 16) + bytes.length * 8
      if (need <= dataCodewords(v, e) * 8) { ver = v; ecl = e; break pick }
    }
  }
  if (!ver) return null

  // ---- bit stream: mode, length, data, terminator, padding
  const bits: number[] = []
  const push = (val: number, len: number) => { for (let i = len - 1; i >= 0; i--) bits.push((val >>> i) & 1) }
  push(4, 4); push(bytes.length, ver < 10 ? 8 : 16)
  for (const b of bytes) push(b, 8)
  const cap = dataCodewords(ver, ecl) * 8
  push(0, Math.min(4, cap - bits.length))
  push(0, (8 - (bits.length % 8)) % 8)
  for (let pad = 0xec; bits.length < cap; pad ^= 0xec ^ 0x11) push(pad, 8)
  const data: number[] = []
  for (let i = 0; i < bits.length; i += 8) data.push(bits.slice(i, i + 8).reduce((a, b) => (a << 1) | b, 0))
  const code = interleave(data, ver, ecl)

  // ---- matrix with function patterns
  const size = ver * 4 + 17
  const m: boolean[][] = Array.from({ length: size }, () => new Array<boolean>(size).fill(false))
  const fn: boolean[][] = Array.from({ length: size }, () => new Array<boolean>(size).fill(false))
  const set = (x: number, y: number, dark: boolean) => { m[y][x] = dark; fn[y][x] = true }

  for (let i = 0; i < size; i++) { set(6, i, i % 2 === 0); set(i, 6, i % 2 === 0) }
  for (const [cx, cy] of [[3, 3], [size - 4, 3], [3, size - 4]]) {
    for (let dy = -4; dy <= 4; dy++) for (let dx = -4; dx <= 4; dx++) {
      const d = Math.max(Math.abs(dx), Math.abs(dy)), x = cx + dx, y = cy + dy
      if (x >= 0 && x < size && y >= 0 && y < size) set(x, y, d !== 2 && d !== 4)
    }
  }
  const al = alignPositions(ver), na = al.length
  for (let i = 0; i < na; i++) for (let j = 0; j < na; j++) {
    if ((i === 0 && j === 0) || (i === 0 && j === na - 1) || (i === na - 1 && j === 0)) continue
    for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) set(al[i] + dx, al[j] + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1)
  }
  const drawFormat = (mask: number) => {
    const d = (FORMAT_BITS[ecl] << 3) | mask
    let rem = d
    for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537)
    const b = ((d << 10) | rem) ^ 0x5412
    const bit = (i: number) => ((b >>> i) & 1) !== 0
    for (let i = 0; i <= 5; i++) set(8, i, bit(i))
    set(8, 7, bit(6)); set(8, 8, bit(7)); set(7, 8, bit(8))
    for (let i = 9; i < 15; i++) set(14 - i, 8, bit(i))
    for (let i = 0; i < 8; i++) set(size - 1 - i, 8, bit(i))
    for (let i = 8; i < 15; i++) set(8, size - 15 + i, bit(i))
    set(8, size - 8, true)
  }
  drawFormat(0)
  if (ver >= 7) {
    let rem = ver
    for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25)
    const b = (ver << 12) | rem
    for (let i = 0; i < 18; i++) {
      const dark = ((b >>> i) & 1) !== 0, a = size - 11 + (i % 3), c = Math.floor(i / 3)
      set(a, c, dark); set(c, a, dark)
    }
  }

  // ---- data modules in the zigzag order
  let k = 0
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5
    for (let vert = 0; vert < size; vert++) for (let j = 0; j < 2; j++) {
      const x = right - j, up = ((right + 1) & 2) === 0, y = up ? size - 1 - vert : vert
      if (!fn[y][x] && k < code.length * 8) { m[y][x] = ((code[k >>> 3] >>> (7 - (k & 7))) & 1) !== 0; k++ }
    }
  }

  // ---- choose the mask with the lowest penalty (run and block rules)
  const MASKS: ((x: number, y: number) => boolean)[] = [
    (x, y) => (x + y) % 2 === 0, (_x, y) => y % 2 === 0, (x) => x % 3 === 0, (x, y) => (x + y) % 3 === 0,
    (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0, (x, y) => ((x * y) % 2) + ((x * y) % 3) === 0,
    (x, y) => (((x * y) % 2) + ((x * y) % 3)) % 2 === 0, (x, y) => (((x + y) % 2) + ((x * y) % 3)) % 2 === 0,
  ]
  const apply = (mask: number) => {
    for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) if (!fn[y][x] && MASKS[mask](x, y)) m[y][x] = !m[y][x]
  }
  const penalty = () => {
    let p = 0, dark = 0
    for (let y = 0; y < size; y++) {
      let runR = 0, runC = 0
      for (let x = 0; x < size; x++) {
        if (m[y][x]) dark++
        runR = x > 0 && m[y][x] === m[y][x - 1] ? runR + 1 : 1
        if (runR === 5) p += 3; else if (runR > 5) p++
        runC = x > 0 && m[x][y] === m[x - 1][y] ? runC + 1 : 1
        if (runC === 5) p += 3; else if (runC > 5) p++
        if (x > 0 && y > 0 && m[y][x] === m[y][x - 1] && m[y][x] === m[y - 1][x] && m[y][x] === m[y - 1][x - 1]) p += 3
      }
    }
    return p + Math.floor(Math.abs(dark * 20 - size * size * 10) / (size * size)) * 10
  }
  let best = 0, bestP = Infinity
  for (let i = 0; i < 8; i++) {
    apply(i); drawFormat(i)
    const p = penalty()
    if (p < bestP) { bestP = p; best = i }
    apply(i)
  }
  apply(best); drawFormat(best)
  return m
}

/** One SVG path for all dark modules; `quiet` is the margin in modules. */
export function qrPath(matrix: boolean[][], quiet = 2): { d: string; size: number } {
  let d = ''
  matrix.forEach((row, y) => {
    let x = 0
    while (x < row.length) {
      if (!row[x]) { x++; continue }
      let w = 1
      while (x + w < row.length && row[x + w]) w++
      d += `M${x + quiet} ${y + quiet}h${w}v1h-${w}z`
      x += w
    }
  })
  return { d, size: matrix.length + quiet * 2 }
}
