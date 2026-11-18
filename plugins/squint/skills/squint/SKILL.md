---
name: squint
description: How to check colour contrast, build against a design, and keep a page fast, with the `@pixieofhugs/squint` package. Use before adding or changing a colour token, a theme, a component's colours or a font; when a contrast test fails; when building from a design hand-off; or when wiring a new page or surface.
---

# squint

Squint looks hard at what a green build cannot see: colours that miss WCAG,
designs that quietly flatten, and pages that get slower one import at a time.

The package is `@pixieofhugs/squint` (`npm i -D @pixieofhugs/squint`). It holds:

| Export | What it does |
|---|---|
| `parseColor`, `contrastRatio`, `compositeOver`, `relativeLuminance`, `deltaE76`, `requiredRatio`, `AA_NORMAL`, `AA_LARGE`, `AAA_NORMAL` | WCAG maths. `parseColor` returns null for anything it cannot read. |
| `saturate`, `screenOver` | `filter: saturate()` and `mix-blend-mode: screen`, for grounds drawn with them. |
| `readThemes`, `resolveVar` | CSS custom properties per theme, through every `var()` hop. |
| `scanPageForContrast` | Runs in the page (`page.evaluate`): every visible text node against the backdrop it actually sits on. |
| `triageFindings`, `baselineKey`, `resolveFillBand` | Node-side: decides what the scan's findings mean, against a shrink-only baseline. |

## Contrast: the rules

1. **Unreadable is a failure, never a skip.** `parseColor` gives null for a
   gradient, an image or an unfamiliar `color-mix()`. A test that skips null
   reads green over the riskiest surfaces. axe-core does exactly this: its
   "incomplete" bucket is where gradients go.
2. **Composite alpha before you measure.** A translucent ink measured as if it
   were solid passes when it fails. `contrastRatio` composites the text for you
   and throws on a translucent surface: composite that surface over the ground
   it is mounted on first.
3. **Measure the pair on screen.** A wash, scrim or tint between the surface
   and the text moves the ratio, often by the whole margin. Stack every layer.
4. **The worst point is the measurement.** On a gradient or a ramp, take the
   minimum ratio across its stops (`resolveFillBand` does this), never the
   average or the middle.
5. **Every lowered floor states its reason.** 4.5:1 for normal text. 3:1 needs
   one of two reasons: large text (`requiredRatio` decides), or not text at all
   (WCAG 1.4.11: borders, icons, hairlines). Write the reason down so a review
   can tell it from a floor lowered to make a test pass.
6. **Contrast cannot see ink beside ink.** Two text tiers can both pass on the
   page and be indistinguishable from each other. Use `deltaE76` with a floor;
   ~2.3 is just noticeable.
7. **Fold near-miss colours.** A colour a few ΔE off a core palette colour is
   slop, not intent: point it at the core with `var()` and prove the fold with
   the token test.

## Two halves, two shapes of bug

**The token test** (unit test, every PR) catches the *value* shape: a token
that misses AA on the surface it is meant to sit on.

```ts
import { readFileSync } from "node:fs";
import { contrastRatio, parseColor, readThemes, resolveVar, AA_NORMAL } from "@pixieofhugs/squint";

const themes = readThemes(readFileSync("src/index.css", "utf8"));
const PAIRS = [{ text: "--text-muted", surface: "--page" }];

for (const theme of ["light", "dark"])
  for (const { text, surface } of PAIRS) {
    const ink = parseColor(resolveVar(text, theme, themes) ?? "");
    const ground = parseColor(resolveVar(surface, theme, themes) ?? "");
    if (!ink || !ground) throw new Error(`${text} on ${surface} does not resolve in ${theme}`);
    if (contrastRatio(ink, ground) < AA_NORMAL) throw new Error(`${text} on ${surface} fails in ${theme}`);
  }
```

`readThemes` defaults to `:root` (the base) and `[data-theme="dark"]`. Pass your
own map when themes live elsewhere: `readThemes(css, { light: [":root"], dark: [".dark"] })`.
A selector must match the rule's text exactly, on one line.

Prefer pairs **enumerated** from the code surfaces use over pairs typed by hand:
a hand list goes stale when nobody adds the new row.

**The rendered sweep** (Playwright, nightly) catches the *pairing* shape: a
component that picked the wrong token. Only computed style shows that.

```ts
import { scanPageForContrast, triageFindings } from "@pixieofhugs/squint";
import { BASELINE } from "./contrastBaseline";

const findings = await page.evaluate(scanPageForContrast, '[data-contrast-exempt]');
const { failures, stale, unmeasurable } = triageFindings(theme, findings, BASELINE);
expect(failures).toEqual([]);
expect(stale).toEqual([]);
```

- The scanner skips `aria-hidden` text, disabled controls and anything inside
  the selector you pass. An exemption is for a deliberate, permanent 1:1 (a
  redaction bar), not for a failure you have not fixed.
- The baseline is keyed by colour pair, not DOM node, and only shrinks: a
  baselined pair that now passes comes back in `stale`, and the fix deletes its
  entry. Never add an entry for new work.
- `unmeasurable` is keyed by the CSS that defeated it. Ratchet it by count; it
  never goes in the baseline.
- Sweep every theme and both a phone and a desktop viewport.

## axe, if you want it as well

`@axe-core/playwright` covers rules beyond contrast (names, roles, landmarks).
Run it beside the scanner, not instead of it:

```ts
import AxeBuilder from "@axe-core/playwright";

const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
expect(results.violations).toEqual([]);
```

Read `results.incomplete` as unchecked, never as passed: every
`color-contrast` entry there is a backdrop axe could not measure, so the
scanner has to.

## Building against a design

**The design value is the default. Any deviation names itself.**

House rules (tokens, minimum text size, touch targets, contrast) win where they
genuinely conflict, never by default and never silently. When one overrides a
design value, a one-line comment names the rule and the value, and the PR body
lists every deviation: what the design said, what shipped, which rule forced it.
The usual failure is not one big substitution but many small, defensible
ones: every size snapped to the nearest token, every font mapped to whichever
one already existed. Together they flatten distinct designs into one.

- **Touch targets win, but re-solve the layout.** A 44×44 minimum dropped into a
  layout drawn for smaller marks overlaps and overflows.
- **Real contrast failures win.** Ship the nearest compliant value, say so in a
  comment, flag it for design.
- **Vendor, build, delete.** Subagents cannot open a design link from a
  worktree, and a design summarised in an issue is prose, which cannot carry
  geometry. Copy the design files into the repo for the life of the work, port
  from them (port shipped code, do not reimplement it), and delete them in the
  last PR.
- **A green build does not prove the design.** Type checks, lint and unit tests
  all pass on a page with missing marks and fonts that never loaded. Check
  rendered structure (the mark renders, the font resolves to the intended
  family, the ratio clears), take screenshots at phone and desktop width, and
  leave motion and feel to a human.

## Keeping a page fast

**A new surface must not enlarge the initial payload**: the entry chunk plus
everything preloaded for it. Measure that number in CI and fail when it grows
past a ratchet; lower the line when you move weight off it.

The ways it breaks:

1. **A static import from something eager.** A registry that imports every
   variant statically puts them all in the entry chunk. Register loaders
   (`() => import(...)`), not modules.
2. **A barrel.** `export * from` pulls the whole directory into whichever chunk
   touches one name.
3. **Splitting routes without splitting the weight.** If a split doesn't move
   the number, something else still holds the weight. Find it first.
4. **A font on the critical path.** A family only one surface uses belongs in a
   stylesheet that surface loads, not the one that blocks first paint. Test
   that every family named in source ships and every shipped family is named.

Also:

- **Preloading can make it much worse.** Warming every chunk after mount
  saturates a slow link and starves the requests the page needs.
- **Slow is not always bytes.** A page that loads the document twice (a
  redirect on a guest's 401, say) is a different bug from a wide waterfall.
  Read what the page actually requested.
- **Throttle or do not bother.** Against localhost every round trip is ~1ms and
  a deep waterfall looks perfect. Time-to-content beats LCP when a nav paints
  first. Run timing nightly; a stopwatch on a shared runner flaps per-PR.
- **Vector is not automatically light.** An SVG with a per-render filter
  (turbulence, displacement, big blur) repeated per card costs paint no byte
  budget catches; rasterise it. A mask needs only its alpha channel.
