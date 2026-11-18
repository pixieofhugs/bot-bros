/** One `color-mix()` shape, a fade to transparent; anything else stays null. */
import { describe, it } from "node:test";
import { each, expect } from "./expect.ts";

import { contrastRatio, parseColor } from "../src/contrast.ts";

describe("parseColor: color-mix fade to transparent", () => {
  it("reads a fade as the base colour carrying that percentage as alpha", () => {
    // Premultiplied mixing with `transparent` leaves channels alone.
    expect(parseColor("color-mix(in srgb, #f0e6d0 74%, transparent)")).toEqual({
      r: 240,
      g: 230,
      b: 208,
      a: 0.74,
    });
  });

  it("agrees with the rgba() spelling of the same colour, to the ratio", () => {
    const sheet = parseColor("#1c1b24")!;
    const mixed = parseColor("color-mix(in srgb, #f0e6d0 74%, transparent)")!;
    const rgba = parseColor("rgba(240, 230, 208, 0.74)")!;
    expect(contrastRatio(mixed, sheet)).toBeCloseTo(contrastRatio(rgba, sheet), 10);
  });

  it("resolves a nested spelling of the base colour", () => {
    expect(parseColor("color-mix(in srgb, rgb(255 255 255) 30%, transparent)")).toEqual({
      r: 255,
      g: 255,
      b: 255,
      a: 0.3,
    });
  });

  each([
    // Two real colours: no mixing math here.
    "color-mix(in srgb, #f0e6d0 74%, #1c1b24)",
    // Another colour space does not interpolate the same way.
    "color-mix(in oklab, #f0e6d0 74%, transparent)",
    // A translucent base would need real premultiplied compositing.
    "color-mix(in srgb, rgba(240, 230, 208, 0.5) 74%, transparent)",
    // Not a colour at all.
    "color-mix(in srgb, var(--never-declared) 74%, transparent)",
  ])("returns null for %s rather than guessing", (value) => {
    expect(parseColor(value)).toBeNull();
  });
});
