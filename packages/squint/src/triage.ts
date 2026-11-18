/**
 * Node-side triage for a rendered sweep. The baseline is keyed by colour pair,
 * not DOM node, and only shrinks; unmeasurable backdrops are never baselined.
 */

import {
  compositeOver,
  contrastRatio,
  parseColor,
  relativeLuminance,
  type Rgba,
} from './contrast.ts';
import type { Finding } from './scan.ts';

export type BaselineEntry = {
  ratio: number;
  issue: number;
  where: string;
};

/** `required` is identity: a pair can pass as large type and fail as body copy. */
export function baselineKey(theme: string, text: string, background: string, required: number): string {
  return `${theme} | ${text} on ${background} @${required}`;
}

/*
 * A gradient's painted luminance lies between its stops', so unless the ink falls
 * inside that band, the band edge nearest the ink is an exact worst case.
 */

/** Darkest/lightest opaque colour under text, by WCAG luminance, not stop order. */
export type FillBand = { lo: Rgba; hi: Rgba };

type Resolved = Finding & { background: string };

/** Layered fills multiply their stops; past this many the band is not worth computing. */
const MAX_CANDIDATES = 512;

/** `background-image` layers, in CSS order — the FIRST one paints on top. */
function splitLayers(css: string): string[] {
  const layers: string[] = [];
  let depth = 0;
  let start = 0;
  for (let index = 0; index < css.length; index += 1) {
    const char = css[index];
    if (char === '(') depth += 1;
    else if (char === ')') depth -= 1;
    else if (char === ',' && depth === 0) {
      layers.push(css.slice(start, index).trim());
      start = index + 1;
    }
  }
  layers.push(css.slice(start).trim());
  return layers.filter(Boolean);
}

/** Null for an unreadable layer: a dropped stop narrows the band and hides a failure. */
const STOP = /(?:rgba?|color)\([^()]*\)|\btransparent\b/g;

function stopsOf(layer: string): Rgba[] | null {
  if (layer === 'none') return [];
  if (!/gradient\(/.test(layer)) return null; // url(), cross-fade(), anything else
  const tokens = layer.match(STOP);
  if (!tokens) return null;
  const stops: Rgba[] = [];
  for (const token of tokens) {
    const color = parseColor(token);
    if (color === null) return null;
    stops.push(color);
  }
  return stops.length ? stops : null;
}

/** Source-over with a possibly translucent backdrop (`compositeOver` assumes opaque). */
function sourceOver(fore: Rgba, back: Rgba): Rgba {
  const alpha = fore.a + back.a * (1 - fore.a);
  if (alpha === 0) return { r: 0, g: 0, b: 0, a: 0 };
  const mix = (front: number, behind: number) =>
    (front * fore.a + behind * back.a * (1 - fore.a)) / alpha;
  return { r: mix(fore.r, back.r), g: mix(fore.g, back.g), b: mix(fore.b, back.b), a: alpha };
}

function dedupe(colors: Rgba[]): Rgba[] {
  const seen = new Map<string, Rgba>();
  for (const color of colors) {
    const key = [color.r, color.g, color.b, color.a].map((channel) => channel.toFixed(3)).join('|');
    if (!seen.has(key)) seen.set(key, color);
  }
  return [...seen.values()];
}

/**
 * Luminance band of a fill, or null (image layer, unparseable stop, or never
 * opaque). Candidates over-estimate (every stop over every stop below), which
 * can only push a fill to unmeasurable, never widen a passing ratio.
 * ponytail: stops only, ignoring geometry; a corner the text never touches can
 * make a fill unmeasurable. Upgrade: text box vs fill box + stop offsets, in the scanner.
 */
export function resolveFillBand(finding: Finding): FillBand | null {
  if (finding.backdropCss === null || finding.backdropBase === null) return null;

  const base = parseColor(finding.backdropBase);
  if (base === null) return null;

  let candidates: Rgba[] = [base];
  // Bottom layer first: the last CSS layer paints first.
  for (const layer of splitLayers(finding.backdropCss).reverse()) {
    const stops = stopsOf(layer);
    if (stops === null) return null;
    if (stops.length === 0) continue;
    const next: Rgba[] = [];
    for (const stop of stops) for (const under of candidates) next.push(sourceOver(stop, under));
    candidates = dedupe(next);
    if (candidates.length > MAX_CANDIDATES) return null;
  }

  if (candidates.some((color) => color.a < 0.999)) return null;

  let opaque = candidates.map((color) => ({ ...color, a: 1 }));
  if (finding.backdropOverlay !== null) {
    const overlay = parseColor(finding.backdropOverlay);
    if (overlay === null) return null;
    opaque = opaque.map((color) => compositeOver(overlay, color));
  }

  let lo = opaque[0];
  let hi = opaque[0];
  for (const color of opaque) {
    if (relativeLuminance(color) < relativeLuminance(lo)) lo = color;
    if (relativeLuminance(color) > relativeLuminance(hi)) hi = color;
  }
  return { lo, hi };
}

function showOpaque(color: Rgba): string {
  const [r, g, b] = [color.r, color.g, color.b].map((channel) => Math.round(channel));
  return `rgb(${r}, ${g}, ${b})`;
}

/**
 * Measure against the fill's worst point, or null when the ink's luminance
 * crosses the band (somewhere it is 1:1). Otherwise the ratio is monotone and
 * its minimum is at the band edge nearest the ink.
 */
export function measureOverFill(finding: Finding): Resolved | null {
  const band = resolveFillBand(finding);
  if (band === null) return null;

  const text = parseColor(finding.text);
  if (text === null) return null;

  const lift = (surface: Rgba) =>
    relativeLuminance(compositeOver(text, surface)) - relativeLuminance(surface);
  const atLo = lift(band.lo);
  const atHi = lift(band.hi);
  if (atLo === 0 || atHi === 0 || atLo > 0 !== atHi > 0) return null;

  const ratioLo = contrastRatio(text, band.lo);
  const ratioHi = contrastRatio(text, band.hi);
  const worst = ratioLo <= ratioHi ? band.lo : band.hi;
  return {
    ...finding,
    background: showOpaque(worst),
    ratio: Math.min(ratioLo, ratioHi),
    unresolved: null,
    unresolvedKind: null,
  };
}

export type Triage = {
  failures: Finding[];
  stale: string[];
  /** Unmeasurable text keyed by the defeating CSS (one key = one surface); ratcheted by count. */
  unmeasurable: Map<string, Finding[]>;
  /** Fills resolved here, already measured; kept so the report shows provenance. */
  resolvedFills: Map<string, Finding[]>;
};

export function bucket(into: Map<string, Finding[]>, surface: string, entry: Finding): void {
  const over = into.get(surface);
  if (over) over.push(entry);
  else into.set(surface, [entry]);
}

/** `background === null` means unresolved, not 0:1; the baseline is never consulted for it. */
export function triageFindings(
  theme: string,
  findings: readonly Finding[],
  baseline: Readonly<Record<string, BaselineEntry>> = {},
): Triage {
  const failures: Finding[] = [];
  const stale: string[] = [];
  const unmeasurable = new Map<string, Finding[]>();
  const resolvedFills = new Map<string, Finding[]>();

  for (const raw of findings) {
    let finding: Resolved;
    if (raw.background === null) {
      const measured = measureOverFill(raw);
      const surface = raw.backdropCss ?? 'unknown';
      if (measured === null) {
        bucket(unmeasurable, surface, raw);
        continue;
      }
      bucket(resolvedFills, surface, measured);
      finding = measured;
    } else {
      finding = raw as Resolved;
    }

    const key = baselineKey(theme, finding.text, finding.background, finding.required);
    const allowed: BaselineEntry | undefined = baseline[key];

    if (finding.ratio >= finding.required) {
      if (allowed) stale.push(`${key} now measures ${finding.ratio.toFixed(2)}:1 (owned by #${allowed.issue})`);
      continue;
    }
    if (allowed) continue;
    failures.push(finding);
  }

  return { failures, stale, unmeasurable, resolvedFills };
}
