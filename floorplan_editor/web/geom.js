// Geometry helpers for the editor. Plan units are inches; the SVG world is sheet pixels.

export function formatLength(inches) {
  const sign = inches < 0 ? "-" : "";
  let rest = Math.abs(inches);
  let feet = Math.floor(rest / 12);
  rest -= feet * 12;
  if (Math.round(rest * 10) / 10 >= 12) {
    feet += 1;
    rest = 0;
  }
  return feet ? `${sign}${feet}'${rest.toFixed(1)}"` : `${sign}${rest.toFixed(1)}"`;
}

const NUM = String.raw`\d+(?:\.\d+)?|\.\d+`;
const FRACTION = String.raw`(?:[\s-]+(\d+)\/(\d+))?`;
const FEET_TOKEN = new RegExp(String.raw`(${NUM})\s*(?:'|ft|feet|foot)(?:-(?=\d)|\s*)(?:(${NUM})${FRACTION}\s*(?:"|in(?:ches|ch)?)?)?`, "y");
const INCH_TOKEN = new RegExp(String.raw`(${NUM})${FRACTION}\s*(?:"|in(?:ches|ch)?)`, "y");
const METRIC_TOKEN = new RegExp(String.raw`(${NUM})\s*(mm|cm|m)(?![a-z])`, "y");
const MIXED_TOKEN = new RegExp(String.raw`(${NUM})\s+(\d+)\/(\d+)`, "y");
const PLAIN_TOKEN = new RegExp(String.raw`(${NUM})`, "y");
const MM_PER_INCH = 25.4;

function lengthTokens(text) {
  const src = String(text).toLowerCase().replace(/[\u2019\u2032]/g, "'").replace(/[\u201d\u2033]/g, '"').replace(/\u00d7/g, "*");
  const tokens = [];
  let i = 0;
  const frac = (m, a, b) => (m[a] ? Number(m[a]) / Number(m[b]) : 0);
  while (i < src.length) {
    const ch = src[i];
    if (/\s/.test(ch)) {
      i++;
      continue;
    }
    if ("+-*/()".includes(ch)) {
      tokens.push(ch);
      i++;
      continue;
    }
    let m = null;
    for (const [re, value] of [
      [FEET_TOKEN, (r) => Number(r[1]) * 12 + Number(r[2] || 0) + frac(r, 3, 4)],
      [INCH_TOKEN, (r) => Number(r[1]) + frac(r, 2, 3)],
      [METRIC_TOKEN, (r) => (Number(r[1]) * { mm: 1, cm: 10, m: 1000 }[r[2]]) / MM_PER_INCH],
      [MIXED_TOKEN, (r) => Number(r[1]) + frac(r, 2, 3)],
      [PLAIN_TOKEN, (r) => Number(r[1])],
    ]) {
      re.lastIndex = i;
      m = re.exec(src);
      if (m) {
        tokens.push(value(m));
        i = re.lastIndex;
        break;
      }
    }
    if (!m) return null;
  }
  return tokens;
}

/** Evaluate a length expression to inches: 10'6", 10' 6 1/2", 126, 3ft + 4in, 48/2, (10'-3")*2, 90cm. */
export function parseLength(text) {
  const tokens = lengthTokens(text);
  if (!tokens || !tokens.length) return NaN;
  let pos = 0;
  const peek = () => tokens[pos];
  const expr = () => {
    let v = term();
    while (peek() === "+" || peek() === "-") v = tokens[pos++] === "+" ? v + term() : v - term();
    return v;
  };
  const term = () => {
    let v = factor();
    while (peek() === "*" || peek() === "/") v = tokens[pos++] === "*" ? v * factor() : v / factor();
    return v;
  };
  const factor = () => {
    const t = tokens[pos++];
    if (t === "-") return -factor();
    if (t === "+") return factor();
    if (t === "(") {
      const v = expr();
      if (tokens[pos++] !== ")") throw new Error("missing )");
      return v;
    }
    if (typeof t === "number") return t;
    throw new Error("unexpected");
  };
  try {
    const v = expr();
    return pos === tokens.length && Number.isFinite(v) ? v : NaN;
  } catch {
    return NaN;
  }
}

/** Typed input while drawing from `from`: "L" along `dir`, "L<A" at A degrees (counterclockwise from right), or "dx, dy". */
export function parseTyped(text, from, dir) {
  const raw = String(text).trim();
  const comma = splitTop(raw, ",");
  if (comma.length === 2) {
    const dx = parseLength(comma[0]);
    const dy = parseLength(comma[1]);
    if (!Number.isFinite(dx) || !Number.isFinite(dy)) return null;
    return [from[0] + dx, from[1] + dy];
  }
  const [lenText, angleText] = raw.split("<");
  const len = parseLength(lenText);
  if (!Number.isFinite(len)) return null;
  if (angleText !== undefined) {
    const a = (Number(angleText) * Math.PI) / 180;
    if (!Number.isFinite(a)) return null;
    return [from[0] + len * Math.cos(a), from[1] - len * Math.sin(a)];
  }
  const d = Math.hypot(dir[0], dir[1]) || 1;
  const [ux, uy] = Math.hypot(dir[0], dir[1]) ? [dir[0] / d, dir[1] / d] : [1, 0];
  return [from[0] + ux * len, from[1] + uy * len];
}

function splitTop(text, sep) {
  const parts = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < text.length; i++) {
    if (text[i] === "(") depth++;
    else if (text[i] === ")") depth--;
    else if (text[i] === sep && depth === 0) {
      parts.push(text.slice(start, i));
      start = i + 1;
    }
  }
  parts.push(text.slice(start));
  return parts;
}

/** Wall direction (unit), left normal, and length. */
export function wallFrame(w) {
  const len = wallLength(w) || 1;
  const ux = (w.x2 - w.x1) / len;
  const uy = (w.y2 - w.y1) / len;
  return { len, ux, uy, nx: -uy, ny: ux };
}

/** Degrees counterclockwise from right, as people read a drawing (y grows down on the sheet). */
export function wallAngle(w) {
  const a = (Math.atan2(-(w.y2 - w.y1), w.x2 - w.x1) * 180) / Math.PI;
  return round2(((a % 360) + 360) % 360);
}

export function fixtureCorners(f) {
  return [
    [-f.w / 2, -f.h / 2],
    [f.w / 2, -f.h / 2],
    [f.w / 2, f.h / 2],
    [-f.w / 2, f.h / 2],
  ].map(([lx, ly]) => {
    const [rx, ry] = rotate(lx, ly, f.rot || 0);
    return [f.x + rx, f.y + ry];
  });
}

export const round2 = (v) => Math.round(v * 100) / 100 + 0;

export class Canvas {
  constructor(plan) {
    const c = plan.canvas;
    this.width = Math.round(c.width_px);
    this.height = Math.round(c.height_px);
    this.scale = c.px_per_inch;
    this.ox = c.origin_px[0];
    this.oy = c.origin_px[1];
  }
  pt(x, y) {
    return [this.ox + x * this.scale, this.oy + y * this.scale];
  }
  toIn(px, py) {
    return [(px - this.ox) / this.scale, (py - this.oy) / this.scale];
  }
}

export function wallLength(w) {
  return Math.hypot(w.x2 - w.x1, w.y2 - w.y1);
}

export function project(w, x, y) {
  const len = wallLength(w);
  if (!len) return { t: 0, x: w.x1, y: w.y1, len: 0 };
  const ux = (w.x2 - w.x1) / len;
  const uy = (w.y2 - w.y1) / len;
  const t = Math.max(0, Math.min(len, (x - w.x1) * ux + (y - w.y1) * uy));
  return { t, x: w.x1 + ux * t, y: w.y1 + uy * t, len };
}

export function distSeg(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const l2 = dx * dx + dy * dy;
  let t = l2 ? ((px - ax) * dx + (py - ay) * dy) / l2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + dx * t), py - (ay + dy * t));
}

export function pointInPoly(x, y, pts) {
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const [xi, yi] = pts[i];
    const [xj, yj] = pts[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** Snap to a wall endpoint, else onto a wall centerline, else to the grid. Inches in and out. */
export function snapPoint(plan, x, y, tol, { grid = 0, skip = new Set() } = {}) {
  let best = null;
  for (const w of plan.walls || []) {
    if (skip.has(w.id)) continue;
    for (const [ex, ey] of [
      [w.x1, w.y1],
      [w.x2, w.y2],
    ]) {
      const d = Math.hypot(ex - x, ey - y);
      if (d <= tol && (!best || d < best.d)) best = { d, x: ex, y: ey };
    }
  }
  if (best) return { x: best.x, y: best.y, kind: "endpoint" };
  for (const w of plan.walls || []) {
    if (skip.has(w.id)) continue;
    const p = project(w, x, y);
    const d = Math.hypot(p.x - x, p.y - y);
    if (d <= tol && (!best || d < best.d)) best = { d, x: p.x, y: p.y };
  }
  if (best) return { x: best.x, y: best.y, kind: "wall" };
  if (grid) return { x: Math.round(x / grid) * grid, y: Math.round(y / grid) * grid, kind: "grid" };
  return { x, y, kind: "none" };
}

export function lockAxis(anchor, x, y) {
  return Math.abs(x - anchor[0]) >= Math.abs(y - anchor[1]) ? [x, anchor[1]] : [anchor[0], y];
}

/** Arc path, the same math as the engine's arc_path. */
export function arcPath(op) {
  const [cx, cy] = op.c;
  const r = op.r;
  const a = (op.start * Math.PI) / 180;
  const b = (op.end * Math.PI) / 180;
  const delta = (((op.end - op.start) % 360) + 360) % 360;
  const large = delta > 180 ? 1 : 0;
  const f = (v) => v.toFixed(2);
  return `M ${f(cx + r * Math.cos(a))} ${f(cy + r * Math.sin(a))} A ${f(r)} ${f(r)} 0 ${large} 1 ${f(cx + r * Math.cos(b))} ${f(cy + r * Math.sin(b))}`;
}

function opDistance(op, x, y) {
  if (op.op === "polygon") {
    if (pointInPoly(x, y, op.points)) return 0;
    let d = Infinity;
    const p = op.points;
    for (let i = 0; i < p.length; i++) {
      const [ax, ay] = p[i];
      const [bx, by] = p[(i + 1) % p.length];
      d = Math.min(d, distSeg(x, y, ax, ay, bx, by));
    }
    return d;
  }
  if (op.op === "line") return Math.max(0, distSeg(x, y, ...op.a, ...op.b) - op.width / 2);
  if (op.op === "arc") {
    const [cx, cy] = op.c;
    const angle = (((Math.atan2(y - cy, x - cx) * 180) / Math.PI) % 360 + 360) % 360;
    const delta = (((op.end - op.start) % 360) + 360) % 360;
    const into = (((angle - op.start) % 360) + 360) % 360;
    if (into <= delta) return Math.abs(Math.hypot(x - cx, y - cy) - op.r);
    return Infinity;
  }
  if (op.op === "text") {
    const [tx, ty] = op.at;
    const hw = (op.text.length * op.size * 0.3) + 2;
    const hh = op.size / 2 + 2;
    const dx = Math.max(0, Math.abs(x - tx) - hw);
    const dy = Math.max(0, Math.abs(y - ty) - hh);
    return Math.hypot(dx, dy);
  }
  return Infinity;
}

function opBox(op, box) {
  const pts =
    op.op === "polygon" ? op.points : op.op === "line" ? [op.a, op.b] : op.op === "arc" ? [[op.c[0] - op.r, op.c[1] - op.r], [op.c[0] + op.r, op.c[1] + op.r]] : [op.at];
  for (const [x, y] of pts) {
    box[0] = Math.min(box[0], x);
    box[1] = Math.min(box[1], y);
    box[2] = Math.max(box[2], x);
    box[3] = Math.max(box[3], y);
  }
}

/** Group scene ops by object: Map key -> {kind, id, ops, area}. */
export function indexScene(ops) {
  const objects = new Map();
  for (const op of ops) {
    const [kind, id] = op.obj;
    const key = `${kind}:${id}`;
    let entry = objects.get(key);
    if (!entry) {
      entry = { key, kind, id, ops: [], box: [Infinity, Infinity, -Infinity, -Infinity] };
      objects.set(key, entry);
    }
    entry.ops.push(op);
    opBox(op, entry.box);
  }
  for (const entry of objects.values()) {
    const [x0, y0, x1, y1] = entry.box;
    entry.area = Math.max(1, (x1 - x0) * (y1 - y0));
  }
  return objects;
}

/** A pickable area that is not drawn by the scene, such as a doorway or an erase region. */
export function hitArea(key, points) {
  const [kind, ...rest] = key.split(":");
  const entry = { key, kind, id: rest.join(":"), ops: [{ op: "polygon", points }], box: [Infinity, Infinity, -Infinity, -Infinity] };
  opBox(entry.ops[0], entry.box);
  const [x0, y0, x1, y1] = entry.box;
  entry.area = Math.max(1, (x1 - x0) * (y1 - y0));
  return entry;
}

/** The object under (x, y) in sheet pixels: nearest first, smaller objects win ties. */
export function pick(objects, x, y, tol, extra = []) {
  let best = null;
  for (const entry of [...objects.values(), ...extra]) {
    const [x0, y0, x1, y1] = entry.box;
    if (x < x0 - tol || x > x1 + tol || y < y0 - tol || y > y1 + tol) continue;
    let d = Infinity;
    for (const op of entry.ops) d = Math.min(d, opDistance(op, x, y));
    if (d > tol) continue;
    if (!best || d < best.d - 0.01 || (Math.abs(d - best.d) <= 0.01 && entry.area < best.entry.area)) best = { d, entry };
  }
  return best ? best.entry : null;
}

export function rotate(lx, ly, deg) {
  const a = (deg * Math.PI) / 180;
  return [lx * Math.cos(a) - ly * Math.sin(a), lx * Math.sin(a) + ly * Math.cos(a)];
}
