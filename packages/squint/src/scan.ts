/**
 * In-page half of a rendered contrast sweep. Must import nothing:
 * `page.evaluate` ships the function source, not its module graph, so the WCAG
 * math is inlined (`./contrast.ts` is the node-side source of truth).
 */
/** One measured text node. `background: null` = could not resolve to a solid. */
export type Finding = {
  text: string;
  background: string | null;
  unresolved: string | null;
  /**
   * `opaque-gradient`: a real fill varying under the text; `other`: an image or
   * unparseable colour. All-translucent gradients are textures (`textureOf`).
   */
  unresolvedKind: 'opaque-gradient' | 'other' | null;
  /** CSS that defeated resolution; the identity of an unresolved finding (stable, unlike the DOM path). */
  backdropCss: string | null;
  /**
   * For `resolveFillBand`, only when `background` is null: `backdropBase`
   * is the fill element's own background-color, `backdropOverlay` everything
   * accumulated above the fill (null if the text sits directly on it).
   */
  backdropBase: string | null;
  backdropOverlay: string | null;
  /** Images ruled out as painting clear of the text (`paintsClearOf`); reported, never silent. */
  clearedLayers: string[];
  /** Path of the element drawing each cleared layer, index-aligned with `clearedLayers`. */
  clearedLayerOwners: string[];
  ratio: number;
  required: number;
  fontSizePx: number;
  fontWeight: number;
  where: string;
  sample: string;
};

/**
 * Measure every visible text node against the backdrop it actually sits on.
 *
 * - An unmeasurable backdrop is reported, never a silent skip (axe's
 *   "incomplete" reads green). Gradients with every stop translucent are
 *   textures: the most-opaque stop is composited and measured (worst case, <2%
 *   error). Opaque-stop fills are reported with facts for `resolveFillBand`,
 *   which judges them node-side.
 * - Skipped: `aria-hidden` text, inactive controls (WCAG 1.4.3), anything matching `exempt`.
 * - `paintsClearOf` is the one layout judgement, so it lives here.
 */
export function scanPageForContrast(exempt?: string): Finding[] {
  type Rgba = { r: number; g: number; b: number; a: number };

  function parse(input: string): Rgba | null {
    const value = input.trim().toLowerCase();
    if (value === "transparent") return { r: 0, g: 0, b: 0, a: 0 };

    // Chromium serializes resolved color-mix() as `color(srgb …)` with 0–1 channels.
    const srgb = /^color\(\s*srgb\s+([^)]+)\)$/.exec(value);
    if (srgb) {
      const parts = srgb[1].replace(/\//g, " ").split(/[\s,]+/).filter(Boolean);
      if (parts.length < 3) return null;
      const [r, g, b] = parts.slice(0, 3).map((token) => Number.parseFloat(token) * 255);
      const a = parts.length > 3 ? Number.parseFloat(parts[3]) : 1;
      if ([r, g, b, a].some((channel) => Number.isNaN(channel))) return null;
      return { r, g, b, a };
    }

    const match = /^rgba?\(([^)]+)\)$/.exec(value);
    if (!match) return null;
    const parts = match[1].replace(/\//g, " ").split(/[\s,]+/).filter(Boolean);
    if (parts.length < 3) return null;
    const [r, g, b] = parts.slice(0, 3).map((token) => Number.parseFloat(token));
    const a = parts.length > 3 ? Number.parseFloat(parts[3]) : 1;
    if ([r, g, b, a].some((channel) => Number.isNaN(channel))) return null;
    return { r, g, b, a };
  }

  /** Source-over where `back` may be translucent; result carries the combined alpha. */
  function srcOver(fore: Rgba, back: Rgba): Rgba {
    const alpha = fore.a + back.a * (1 - fore.a);
    if (alpha === 0) return { r: 0, g: 0, b: 0, a: 0 };
    const mix = (front: number, behind: number) =>
      (front * fore.a + behind * back.a * (1 - fore.a)) / alpha;
    return { r: mix(fore.r, back.r), g: mix(fore.g, back.g), b: mix(fore.b, back.b), a: alpha };
  }

  function luminance(color: Rgba): number {
    const [r, g, b] = [color.r, color.g, color.b].map((channel) => {
      const srgb = channel / 255;
      return srgb <= 0.03928 ? srgb / 12.92 : Math.pow((srgb + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }

  function ratioOf(text: Rgba, surface: Rgba): number {
    const composited = srcOver(text, surface);
    const high = Math.max(luminance(composited), luminance(surface));
    const low = Math.min(luminance(composited), luminance(surface));
    return (high + 0.05) / (low + 0.05);
  }

  /** Baseline-key serialization; alpha must survive or ghosted ink collapses into its solid pair. */
  function show(color: Rgba): string {
    const [r, g, b] = [color.r, color.g, color.b].map((channel) => Math.round(channel));
    if (color.a >= 1) return `rgb(${r}, ${g}, ${b})`;
    return `rgba(${r}, ${g}, ${b}, ${Number(color.a.toFixed(3))})`;
  }

  /**
   * Texture (all stops translucent) → its most-opaque stop; fill → `undefined`;
   * unparseable stop → `null` (never guess).
   */
  function textureOf(image: string): Rgba | null | undefined {
    if (!/gradient\(/.test(image)) return undefined;
    const stops = image.match(/(?:rgba?|color)\([^)]*\)/g);
    if (!stops) return undefined;
    const parsed: Rgba[] = [];
    for (const stop of stops) {
      const color = parse(stop);
      if (color === null) return null;
      if (color.a >= 1) return undefined; // an opaque stop makes this a fill
      parsed.push(color);
    }
    if (parsed.length === 0) return undefined;
    return parsed.reduce((worst, stop) => (stop.a > worst.a ? stop : worst));
  }

  function ariaHidden(element: Element): boolean {
    let node: Element | null = element;
    while (node) {
      if (node.getAttribute("aria-hidden") === "true") return true;
      node = node.parentElement;
    }
    return false;
  }

  /** WCAG 1.4.3 exempts inactive controls. Walks up: the wash and text are rarely one element. */
  function inertControl(element: Element): boolean {
    let node: Element | null = element;
    while (node) {
      if (node.matches("button:disabled, input:disabled, select:disabled, textarea:disabled, fieldset:disabled, [aria-disabled='true']")) {
        return true;
      }
      node = node.parentElement;
    }
    return false;
  }

  /** A deliberate, permanent exemption the caller names (e.g. a redaction bar), not a baseline. */
  function exemptMark(element: Element, selector: string): boolean {
    let node: Element | null = element;
    while (node) {
      if (node.matches(selector)) return true;
      node = node.parentElement;
    }
    return false;
  }

  function ownText(element: Element): string {
    let text = "";
    for (const child of Array.from(element.childNodes)) {
      if (child.nodeType === Node.TEXT_NODE) text += child.textContent ?? "";
    }
    return text.trim();
  }

  function pathOf(element: Element): string {
    const steps: string[] = [];
    let node: Element | null = element;
    for (let depth = 0; node && depth < 4; depth += 1) {
      const classes =
        typeof node.className === "string"
          ? node.className.trim().split(/\s+/).slice(0, 2).filter(Boolean)
          : [];
      steps.unshift(node.tagName.toLowerCase() + classes.map((name) => `.${name}`).join(""));
      node = node.parentElement;
    }
    return steps.join(" > ");
  }

  type Box = { left: number; right: number; top: number; bottom: number };

  /** `element`'s padding box, or its content box when `content` is set. */
  function innerBox(element: Element, style: CSSStyleDeclaration, content: boolean): Box {
    const rect = element.getBoundingClientRect();
    const inset = (side: "Top" | "Right" | "Bottom" | "Left") =>
      (Number.parseFloat(style[`border${side}Width`]) || 0) +
      (content ? Number.parseFloat(style[`padding${side}`]) || 0 : 0);
    return {
      left: rect.left + inset("Left"),
      right: rect.right - inset("Right"),
      top: rect.top + inset("Top"),
      bottom: rect.bottom - inset("Bottom"),
    };
  }

  /**
   * True only when `node`'s background-image PROVABLY paints clear of the
   * text's content box, e.g. a 2px rule under a heading.
   * Anything it cannot compute (`auto`, `cover`, `calc()`, multi-value lists,
   * non-default origin/attachment) counts as covering. A wrong "clear" is a
   * silent pass, so it also refuses:
   * body/html (canvas propagation), `inline` boxes (per-fragment painting), any
   * transform/rotate/scale/translate/zoom up to the root, sticky/fixed and
   * non-visible overflow between text and layer (scrolling moves the text).
   *
   * ponytail: content box stands in for glyphs, so a descender can cross a rule
   * ruled clear. Upgrade: `Range.getClientRects()`.
   */
  function paintsClearOf(node: Element, style: CSSStyleDeclaration, element: Element): boolean {
    if (style.backgroundRepeat !== "no-repeat") return false;
    if (style.backgroundOrigin !== "padding-box" || style.backgroundAttachment !== "scroll") return false;
    if (/^(BODY|HTML)$/.test(node.tagName) || style.display === "inline") return false;
    for (let at: Element | null = element, below = true; at; at = at.parentElement) {
      const its = window.getComputedStyle(at);
      if (its.transform !== "none") return false;
      // Empty means the engine does not report it: nothing set.
      if ([its.rotate, its.scale, its.translate].some((v) => v && v !== "none")) return false;
      if (its.zoom && its.zoom !== "1") return false;
      // Only BETWEEN text and layer; at or above, both move together (a sticky header).
      if (below && at !== node && (its.position === "sticky" || its.position === "fixed")) return false;
      if (below && (its.overflowX !== "visible" || its.overflowY !== "visible")) return false;
      if (at === node) below = false;
    }

    const sizes = style.backgroundSize.split(" ");
    const positions = style.backgroundPosition.split(" ");
    if (sizes.length !== 2 || positions.length !== 2) return false;

    const area = innerBox(node, style, false);
    /** Layer extent along one axis, or null if not computable. */
    const span = (size: string, position: string, start: number, end: number): [number, number] | null => {
      const extent = end - start;
      let length: number;
      if (/^[\d.]+px$/.test(size)) length = Number.parseFloat(size);
      else if (/^[\d.]+%$/.test(size)) length = (extent * Number.parseFloat(size)) / 100;
      else return null;
      let offset: number;
      if (/^-?[\d.]+px$/.test(position)) offset = Number.parseFloat(position);
      else if (/^-?[\d.]+%$/.test(position)) offset = ((extent - length) * Number.parseFloat(position)) / 100;
      else return null;
      return [start + offset, start + offset + length];
    };
    const across = span(sizes[0], positions[0], area.left, area.right);
    const down = span(sizes[1], positions[1], area.top, area.bottom);
    if (across === null || down === null) return false;

    // Read lazily: only rare layers get this far.
    const glyphs = innerBox(element, window.getComputedStyle(element), true);
    // Half a pixel of shared edge is rounding, not a ground.
    const slack = 0.5;
    return (
      across[1] <= glyphs.left + slack ||
      across[0] >= glyphs.right - slack ||
      down[1] <= glyphs.top + slack ||
      down[0] >= glyphs.bottom - slack
    );
  }

  /** Walk ancestors until opaque; per node, background-color then background-image over it. */
  function backdropOf(element: Element): {
    color: Rgba | null;
    why: string | null;
    kind: Finding["unresolvedKind"];
    css: string | null;
    base: string | null;
    overlay: string | null;
    cleared: string[];
    clearedOwners: string[];
  } {
    let stack: Rgba | null = null;
    let node: Element | null = element;
    const cleared: string[] = [];
    const clearedOwners: string[] = [];

    while (node) {
      const style = window.getComputedStyle(node);

      let layer = parse(style.backgroundColor);
      if (layer === null) {
        return {
          color: null,
          why: `${pathOf(node)} background-color "${style.backgroundColor}" is not a solid color`,
          kind: "other",
          css: style.backgroundColor,
          base: null,
          overlay: null,
          cleared,
          clearedOwners,
        };
      }

      const image = style.backgroundImage && style.backgroundImage !== "none";
      if (image && paintsClearOf(node, style, element)) {
        cleared.push(style.backgroundImage);
        clearedOwners.push(pathOf(node));
      } else if (image) {
        const texture = textureOf(style.backgroundImage);
        if (texture === undefined || texture === null) {
          const isGradient = /gradient\(/.test(style.backgroundImage);
          return {
            color: null,
            why: `${pathOf(node)} paints ${style.backgroundImage.slice(0, 80)}`,
            kind: isGradient && texture === undefined ? "opaque-gradient" : "other",
            css: style.backgroundImage,
            // `layer` is still the element's own background-color here.
            base: show(layer),
            overlay: stack === null ? null : show(stack),
            cleared,
            clearedOwners,
          };
        }
        layer = srcOver(texture, layer);
      }

      if (layer.a > 0) {
        stack = stack === null ? layer : srcOver(stack, layer);
        if (stack.a >= 0.999) {
          return { color: { ...stack, a: 1 }, why: null, kind: null, css: null, base: null, overlay: null, cleared, clearedOwners };
        }
      }
      node = node.parentElement;
    }

    const canvas: Rgba = { r: 255, g: 255, b: 255, a: 1 };
    return {
      color: stack === null ? canvas : srcOver(stack, canvas),
      why: null,
      kind: null,
      css: null,
      base: null,
      overlay: null,
      cleared,
      clearedOwners,
    };
  }

  const findings: Finding[] = [];

  for (const element of Array.from(document.body.querySelectorAll<HTMLElement>("*"))) {
    const text = ownText(element);
    if (!text) continue;
    if (ariaHidden(element)) continue;
    if (inertControl(element)) continue;
    if (exempt && exemptMark(element, exempt)) continue;

    const box = element.getBoundingClientRect();
    if (box.width < 1 || box.height < 1) continue;

    const style = window.getComputedStyle(element);
    if (style.visibility === "hidden" || style.display === "none") continue;

    let opacity = Number.parseFloat(style.opacity);
    let ancestor = element.parentElement;
    while (ancestor) {
      opacity *= Number.parseFloat(window.getComputedStyle(ancestor).opacity);
      ancestor = ancestor.parentElement;
    }
    if (opacity <= 0.01) continue;

    const foreground = parse(style.color);
    if (foreground === null || foreground.a * opacity <= 0.01) continue;
    const inked: Rgba = { ...foreground, a: Math.min(1, foreground.a * opacity) };

    const backdrop = backdropOf(element);
    const fontSizePx = Number.parseFloat(style.fontSize);
    const fontWeight = Number.parseInt(style.fontWeight, 10) || 400;
    const large = fontSizePx >= 24 || (fontSizePx >= 18.66 && fontWeight >= 700);
    const required = large ? 3 : 4.5;
    const sample = text.replace(/\s+/g, " ").slice(0, 40);

    findings.push({
      text: show(inked),
      background: backdrop.color === null ? null : show(backdrop.color),
      unresolved: backdrop.why,
      unresolvedKind: backdrop.kind,
      backdropCss: backdrop.css,
      backdropBase: backdrop.base,
      backdropOverlay: backdrop.overlay,
      clearedLayers: backdrop.cleared,
      clearedLayerOwners: backdrop.clearedOwners,
      ratio: backdrop.color === null ? 0 : ratioOf(inked, backdrop.color),
      required,
      fontSizePx,
      fontWeight,
      where: pathOf(element),
      sample,
    });
  }

  return findings;
}
