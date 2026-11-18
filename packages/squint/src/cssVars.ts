/**
 * CSS custom-property resolver. Every `var()` hop re-reads the theme first and
 * the base second: a base-only var can still flip if it points at one the theme rebinds.
 */

export type VarMap = Map<string, string>;

/** Theme → its rule selectors (merged in source order). The FIRST theme is the base. */
export type ThemeSelectors = Record<string, string[]>;

export const DEFAULT_THEMES: ThemeSelectors = {
  light: [":root"],
  dark: ['[data-theme="dark"]'],
};

/** Theme → its var map. The first key is the base. */
export type Themes = Record<string, VarMap>;

const COMMENT = /\/\*[\s\S]*?\*\//g;
const CUSTOM_PROP = /(--[\w-]+)\s*:\s*([^;]+);/g;
const VAR_REF = /var\(\s*(--[\w-]+)\s*(?:,\s*([^)]*))?\)/;

export function stripComments(css: string): string {
  return css.replace(COMMENT, "");
}

export function ruleBodies(css: string, selector: string): string[] {
  return matchRules(css, selector).map((rule) => rule.body);
}

interface Rule {
  at: number;
  body: string;
}

/** Exact selector text on one line: a selector split over lines never matches. */
function matchRules(css: string, selector: string): Rule[] {
  const rules: Rule[] = [];
  let cursor = 0;
  while (cursor < css.length) {
    const start = css.indexOf(selector, cursor);
    if (start === -1) break;
    const open = css.indexOf("{", start);
    if (open === -1) break;
    const between = css.slice(start + selector.length, open).trim();
    const before = start === 0 ? "\n" : css[start - 1];
    if (between !== "" || !/[\s;{}]/.test(before)) {
      cursor = start + selector.length;
      continue;
    }
    let depth = 1;
    let index = open + 1;
    while (index < css.length && depth > 0) {
      if (css[index] === "{") depth += 1;
      else if (css[index] === "}") depth -= 1;
      index += 1;
    }
    rules.push({ at: start, body: css.slice(open + 1, index - 1) });
    cursor = index;
  }
  return rules;
}

function bodiesInSourceOrder(css: string, selectors: string[]): string[] {
  return selectors
    .flatMap((selector) => matchRules(css, selector))
    .sort((a, b) => a.at - b.at)
    .map((rule) => rule.body);
}

export function declarationsIn(bodies: string[]): VarMap {
  const map: VarMap = new Map();
  for (const body of bodies) {
    for (const match of body.matchAll(CUSTOM_PROP)) {
      map.set(match[1], match[2].trim());
    }
  }
  return map;
}

export function readThemes(css: string, selectors: ThemeSelectors = DEFAULT_THEMES): Themes {
  const names = Object.keys(selectors);
  if (names.length === 0) throw new Error("readThemes: no themes given");
  const clean = stripComments(css);
  return Object.fromEntries(
    names.map((name) => [name, declarationsIn(bodiesInSourceOrder(clean, selectors[name]))]),
  );
}

/** Resolve through `var()` hops; null = undeclared or circular (a broken token). */
export function resolveVar(
  name: string,
  theme: string,
  themes: Themes,
  seen: Set<string> = new Set(),
): string | null {
  if (seen.has(name)) return null;
  seen.add(name);

  const own = themes[theme];
  if (own === undefined) throw new Error(`resolveVar: no theme named "${theme}"`);
  const declared = own.get(name) ?? Object.values(themes)[0].get(name);
  if (declared === undefined) return null;
  return resolveValue(declared, theme, themes, seen);
}

function resolveValue(value: string, theme: string, themes: Themes, seen: Set<string>): string | null {
  let current = value.trim();
  let guard = 0;
  let match = VAR_REF.exec(current);
  while (match) {
    if (guard++ > 32) return null;
    const inner = resolveVar(match[1], theme, themes, new Set(seen));
    const replacement = inner ?? (match[2] !== undefined ? match[2].trim() : null);
    if (replacement === null) return null;
    current = current.slice(0, match.index) + replacement + current.slice(match.index + match[0].length);
    match = VAR_REF.exec(current);
  }
  return current;
}
