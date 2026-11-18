/**
 * The contrast triage policy, pure so a PR can test what the nightly
 * `e2e/contrast.spec.ts` decides. `background === null` means "could not
 * resolve", NOT 0:1.
 */
import { describe, it } from "node:test";
import { expect } from "./expect.ts";

import { baselineKey, resolveFillBand, triageFindings, type BaselineEntry } from "../src/triage.ts";
import type { Finding } from "../src/scan.ts";

function finding(overrides: Partial<Finding> = {}): Finding {
  return {
    text: "rgb(0, 0, 0)",
    background: "rgb(255, 255, 255)",
    unresolved: null,
    unresolvedKind: null,
    backdropCss: null,
    backdropBase: null,
    backdropOverlay: null,
    clearedLayers: [],
    clearedLayerOwners: [],
    ratio: 21,
    required: 4.5,
    fontSizePx: 16,
    fontWeight: 400,
    where: "div > div > span",
    sample: "some copy",
    ...overrides,
  };
}

/** A finding the scanner gave up on, over `css`; base transparent (gradient is the surface). */
function unresolved(css: string, overrides: Partial<Finding> = {}): Finding {
  return finding({
    background: null,
    ratio: 0,
    unresolved: `div paints ${css}`,
    unresolvedKind: "opaque-gradient",
    backdropCss: css,
    backdropBase: "rgba(0, 0, 0, 0)",
    backdropOverlay: null,
    ...overrides,
  });
}

/** A paper-stock ramp. */
const PAPER_RAMP = "linear-gradient(180deg, rgb(253, 246, 234) 0%, rgb(236, 228, 210) 100%)";
/** The unaffiliated spectrum, in miniature. */
const RAINBOW = "linear-gradient(90deg, rgb(193, 39, 45) 0%, rgb(202, 138, 4) 50%, rgb(37, 99, 235) 100%)";
/** Translucent wash over an opaque white ramp. */
const WASHED_PAPER =
  "radial-gradient(rgba(0, 0, 0, 0.5) 0%, rgba(0, 0, 0, 0) 70%), " +
  "linear-gradient(180deg, rgb(255, 255, 255) 0%, rgb(255, 255, 255) 100%)";
/** Black to white: every ink hits 1:1 somewhere, so it must stay unmeasurable. */
const STRADDLE = (deg: number) => `linear-gradient(${deg}deg, rgb(0, 0, 0) 0%, rgb(255, 255, 255) 100%)`;

const BASELINE: Record<string, BaselineEntry> = {
  "dark | rgb(111, 174, 0) on rgb(244, 241, 232) @4.5": { ratio: 2.41, issue: 1, where: "div > div" },
};
const [allowlistedKey, allowlistedEntry] = Object.entries(BASELINE)[0];
const parsedKey = /^(\w+) \| (.+) on (rgba?\([^)]*\)) @([\d.]+)$/.exec(allowlistedKey);
if (parsedKey === null) throw new Error(`BASELINE key is not in baselineKey() form: ${allowlistedKey}`);
const [, allowedTheme, allowedText, allowedBackground, allowedRequired] = parsedKey;
const allowlisted = finding({
  text: allowedText,
  background: allowedBackground,
  required: Number(allowedRequired),
  ratio: allowlistedEntry.ratio,
});

describe("triageFindings", () => {
  it("parses a real allowlist key back into the fixture it describes", () => {
    // If this drifts from baselineKey(), the allowlist tests test nothing.
    expect(
      baselineKey(allowedTheme, allowlisted.text, allowedBackground, allowlisted.required),
    ).toBe(allowlistedKey);
    expect(allowlisted.ratio).toBeLessThan(allowlisted.required);
  });

  it("reports an unresolvable backdrop instead of failing it", () => {
    const { failures, unmeasurable } = triageFindings("light", [unresolved(STRADDLE(90))]);

    expect(failures).toHaveLength(0);
    expect(unmeasurable.size).toBe(1);
  });

  it("counts one surface per distinct backdrop, however many nodes sit on it", () => {
    const gilt = STRADDLE(160);
    const rainbow = STRADDLE(90);
    const { unmeasurable } = triageFindings("dark", [unresolved(gilt), unresolved(gilt), unresolved(rainbow)]);

    expect([...unmeasurable.keys()]).toEqual([gilt, rainbow]);
    expect(unmeasurable.get(gilt)).toHaveLength(2);
  });

  it("still fails a genuinely-measured pairing below AA", () => {
    const { failures, unmeasurable } = triageFindings("light", [finding({ ratio: 3.2, required: 4.5 })]);

    expect(failures).toHaveLength(1);
    expect(unmeasurable.size).toBe(0);
  });

  it("passes a measured pairing that clears its requirement", () => {
    const { failures, stale } = triageFindings("light", [finding({ ratio: 4.5, required: 4.5 })]);

    expect(failures).toHaveLength(0);
    expect(stale).toHaveLength(0);
  });

  it("holds its fire on a pairing that is still allowlisted", () => {
    const { failures, stale } = triageFindings(allowedTheme, [allowlisted], BASELINE);

    expect(failures).toHaveLength(0);
    expect(stale).toHaveLength(0);
  });

  it("fails an allowlisted pairing that now clears AA — the list only shrinks", () => {
    const fixed = { ...allowlisted, ratio: allowlisted.required + 0.5 };
    const { failures, stale } = triageFindings(allowedTheme, [fixed], BASELINE);

    expect(failures).toHaveLength(0);
    expect(stale).toHaveLength(1);
    expect(stale[0]).toContain(`#${allowlistedEntry.issue}`);
  });

  it("does not consult the allowlist for an unresolvable backdrop", () => {
    // allowlisted unresolved surfaces were never reported.
    const { failures, stale, unmeasurable } = triageFindings(allowedTheme, [
      { ...unresolved(STRADDLE(90)), text: allowedText },
    ], BASELINE);

    expect(failures).toHaveLength(0);
    expect(stale).toHaveLength(0);
    expect(unmeasurable.size).toBe(1);
  });

  it("leaves a translucent-over-solid ground exactly where it was", () => {
    // Resolved composites must never reach the fill path: asserts branch order.
    const composited = finding({ background: "rgb(247, 244, 238)", ratio: 4.12, required: 4.5 });
    const { failures, unmeasurable } = triageFindings("light", [composited], BASELINE);

    expect(unmeasurable.size).toBe(0);
    expect(failures).toHaveLength(1);
    expect(failures[0].background).toBe("rgb(247, 244, 238)");
  });
});

/**
 * an opaque-stop gradient is measurable unless the ink's luminance falls
 * inside its band; outside it, the nearest band edge is the exact worst case.
 * Read off computed CSS, node-side (the `page.evaluate` payload is untyped).
 */
describe("resolveFillBand", () => {
  it("resolves a two-stop paper ramp to its darkest and lightest stop", () => {
    const band = resolveFillBand(unresolved(PAPER_RAMP));

    expect(band).not.toBeNull();
    expect(band?.lo).toEqual({ r: 236, g: 228, b: 210, a: 1 });
    expect(band?.hi).toEqual({ r: 253, g: 246, b: 234, a: 1 });
  });

  it("composites a translucent wash over the opaque ramp beneath it", () => {
    // First listed layer paints on TOP; misreading gives white, which passes all.
    const band = resolveFillBand(unresolved(WASHED_PAPER));

    expect(band?.hi).toEqual({ r: 255, g: 255, b: 255, a: 1 });
    expect(band?.lo).toEqual({ r: 127.5, g: 127.5, b: 127.5, a: 1 });
  });

  it("composites the translucent stack sitting ABOVE the fill", () => {
    const band = resolveFillBand(unresolved(PAPER_RAMP, { backdropOverlay: "rgba(0, 0, 0, 0.5)" }));

    // 236 * 0.5 = 118, 253 * 0.5 = 126.5.
    expect(band?.lo.r).toBeCloseTo(118, 5);
    expect(band?.hi.r).toBeCloseTo(126.5, 5);
  });

  it("refuses an image layer — there is no stop list to read", () => {
    expect(resolveFillBand(unresolved(`url("/media/plate.png"), ${PAPER_RAMP}`))).toBeNull();
  });

  it("refuses a fill that never reaches opacity — what is under it still shows", () => {
    const leaky = "linear-gradient(180deg, rgba(0, 0, 0, 0.2) 0%, rgb(1, 2, 3) 100%)";

    expect(resolveFillBand(unresolved(leaky))).toBeNull();
  });

  it("paints the fill over the element's own background-color", () => {
    // 200 * 0.8 = 160.
    const leaky = "linear-gradient(180deg, rgba(0, 0, 0, 0.2) 0%, rgb(1, 2, 3) 100%)";
    const band = resolveFillBand(unresolved(leaky, { backdropBase: "rgb(200, 200, 200)" }));

    expect(band?.hi).toEqual({ r: 160, g: 160, b: 160, a: 1 });
    expect(band?.lo).toEqual({ r: 1, g: 2, b: 3, a: 1 });
  });
});

describe("triageFindings over a decorative fill", () => {
  it("measures text on a paper ramp against the worst stop", () => {
    const ink = unresolved(PAPER_RAMP, { text: "rgb(28, 28, 26)" });
    const { failures, unmeasurable } = triageFindings("light", [ink], BASELINE);

    expect(unmeasurable.size).toBe(0);
    expect(failures).toHaveLength(0);
  });

  it("fails ink that clears the light stop but not the dark one", () => {
    // 4.89:1 on the light stop, 4.15:1 on the dark: lighter or average would pass.
    const ink = unresolved(PAPER_RAMP, { text: "rgb(108, 108, 108)" });
    const { failures, unmeasurable } = triageFindings("light", [ink], BASELINE);

    expect(unmeasurable.size).toBe(0);
    expect(failures).toHaveLength(1);
    expect(failures[0].background).toBe("rgb(236, 228, 210)");
    expect(failures[0].ratio).toBeLessThan(4.5);
  });

  it("keeps a fill unmeasurable when the ink's luminance falls inside the band", () => {
    const ink = unresolved(RAINBOW, { text: "rgb(120, 120, 120)" });
    const { failures, unmeasurable } = triageFindings("light", [ink], BASELINE);

    expect(failures).toHaveLength(0);
    expect(unmeasurable.size).toBe(1);
  });

  it("still measures white on that same spectrum — it is outside the band", () => {
    // Exempting the whole gradient would lose it.
    const ink = unresolved(RAINBOW, { text: "rgb(255, 255, 255)" });
    const { failures, unmeasurable } = triageFindings("light", [ink], BASELINE);

    expect(unmeasurable.size).toBe(0);
    expect(failures).toHaveLength(1);
    expect(failures[0].background).toBe("rgb(202, 138, 4)");
  });

  it("reports resolved fills separately so a new failure can be traced to this change", () => {
    const { resolvedFills } = triageFindings("light", [
      unresolved(PAPER_RAMP, { text: "rgb(28, 28, 26)" }),
      unresolved(PAPER_RAMP, { text: "rgb(28, 28, 26)" }),
      unresolved(STRADDLE(90)),
    ]);

    expect([...resolvedFills.keys()]).toEqual([PAPER_RAMP]);
    expect(resolvedFills.get(PAPER_RAMP)).toHaveLength(2);
  });

  it("does not grandfather a fill it could not resolve", () => {
    // Only a MEASURED pairing may consult the allowlist.
    const { failures, stale, unmeasurable } = triageFindings(allowedTheme, [
      { ...unresolved(STRADDLE(45)), text: allowedText, background: null },
    ]);

    expect(failures).toHaveLength(0);
    expect(stale).toHaveLength(0);
    expect(unmeasurable.size).toBe(1);
  });
});
