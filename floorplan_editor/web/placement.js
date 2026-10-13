// Finds a spot for each measurement label near its line that covers as little as possible:
// other labels, plan objects, dimension lines, sheet ink, and panels floating over the drawing.

const INK_SIDE = 1600;
const INK_DARK = 170;
const RINGS = [0, 14, 26, 40, 56, 76, 100, 128];
const ANGLES = 16;

let ink = null;
let inkUrl = "";

/** Keep a small black-and-white copy of the sheet, drawn `worldWidth` wide, so labels can steer clear of its ink. */
export function loadSheetInk(url, worldWidth, onReady) {
  if (url === inkUrl) return;
  inkUrl = url;
  ink = null;
  if (!url) return;
  const img = new Image();
  img.onload = () => {
    if (inkUrl !== url) return;
    const shrink = Math.min(1, INK_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
    const w = Math.max(1, Math.round(img.naturalWidth * shrink));
    const h = Math.max(1, Math.round(img.naturalHeight * shrink));
    const k = w / (worldWidth || img.naturalWidth);
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const g = canvas.getContext("2d", { willReadFrequently: true });
    g.fillStyle = "#fff";
    g.fillRect(0, 0, w, h);
    g.drawImage(img, 0, 0, w, h);
    const rgba = g.getImageData(0, 0, w, h).data;
    // Summed-area table of dark pixels, one row and column larger, so any box's ink count is four lookups.
    const W = w + 1;
    const sat = new Uint32Array(W * (h + 1));
    for (let y = 0; y < h; y++) {
      let row = 0;
      for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4;
        row += rgba[i] * 0.3 + rgba[i + 1] * 0.59 + rgba[i + 2] * 0.11 < INK_DARK ? 1 : 0;
        sat[(y + 1) * W + x + 1] = sat[y * W + x + 1] + row;
      }
    }
    ink = { w, h, k, sat };
    onReady && onReady();
  };
  img.src = url;
}

function inkShare([x0, y0, x1, y1]) {
  if (!ink) return 0;
  const clamp = (v, hi) => Math.max(0, Math.min(hi, Math.round(v * ink.k)));
  const a = clamp(x0, ink.w);
  const b = clamp(x1, ink.w);
  const c = clamp(y0, ink.h);
  const d = clamp(y1, ink.h);
  if (b <= a || d <= c) return 0;
  const W = ink.w + 1;
  const s = ink.sat;
  return (s[d * W + b] - s[c * W + b] - s[d * W + a] + s[c * W + a]) / ((b - a) * (d - c));
}

function overlap(a, b) {
  const w = Math.min(a[2], b[2]) - Math.max(a[0], b[0]);
  const h = Math.min(a[3], b[3]) - Math.max(a[1], b[1]);
  return w > 0 && h > 0 ? w * h : 0;
}

function pointsIn(rect, pts, self) {
  let n = 0;
  for (const [x, y, owner] of pts) if (owner !== self && x >= rect[0] && x <= rect[2] && y >= rect[1] && y <= rect[3]) n++;
  return n;
}

/**
 * Picks label centers, in world units.
 * `labels`: { home: [x, y], w, h, fixed?: [x, y] } with sizes in world units; fixed labels stay where they are.
 * `scene`: { px, boxes, lines, view, blocked, useInk } where px is one screen pixel in world units,
 * boxes and blocked are [x0, y0, x1, y1], lines are [[x, y], [x, y], owner] where owner is the index of
 * the label the line belongs to (a label may sit on its own line), and view is the visible area.
 */
export function placeLabels(labels, scene) {
  const { px, boxes, view, blocked, useInk } = scene;
  const linePts = [];
  for (const [a, b, owner] of scene.lines) {
    const n = Math.max(2, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / (6 * px)));
    for (let i = 0; i <= n; i++) linePts.push([a[0] + ((b[0] - a[0]) * i) / n, a[1] + ((b[1] - a[1]) * i) / n, owner]);
  }
  const placed = [];
  const out = new Array(labels.length);
  labels.forEach((l, i) => {
    if (!l.fixed) return;
    out[i] = l.fixed;
    placed.push(rectAt(l.fixed, l, px));
  });
  labels.forEach((l, i) => {
    if (l.fixed) return;
    const reach = (RINGS[RINGS.length - 1] + l.w / px) * px;
    const near = boxes.filter((b) => b[2] > l.home[0] - reach && b[0] < l.home[0] + reach && b[3] > l.home[1] - reach && b[1] < l.home[1] + reach);
    let best = null;
    for (const r of RINGS) {
      const steps = r ? ANGLES : 1;
      for (let s = 0; s < steps; s++) {
        const t = (s / steps) * Math.PI * 2;
        const at = [l.home[0] + Math.cos(t) * r * px, l.home[1] + Math.sin(t) * r * px];
        const rect = rectAt(at, l, px);
        const area = (rect[2] - rect[0]) * (rect[3] - rect[1]);
        let cost = r * 0.12;
        for (const p of placed) cost += (overlap(rect, p) / area) * 1000;
        for (const b of near) cost += Math.min(1, overlap(rect, b) / area) * 60;
        for (const b of blocked) cost += (overlap(rect, b) / area) * 400;
        cost += pointsIn(rect, linePts, i) * 6;
        if (useInk) cost += Math.min(1, inkShare(rect) * 5) * 90;
        if (rect[0] < view[0] || rect[1] < view[1] || rect[2] > view[2] || rect[3] > view[3]) cost += 300;
        if (!best || cost < best.cost - 1e-9) best = { cost, at };
      }
      if (best && best.cost < 0.5 + r * 0.12) break;
    }
    out[i] = best.at;
    placed.push(rectAt(best.at, l, px));
  });
  return out;
}

function rectAt([x, y], l, px) {
  const pad = 4 * px;
  return [x - l.w / 2 - pad, y - l.h / 2 - pad, x + l.w / 2 + pad, y + l.h / 2 + pad];
}
