import { describe, it } from "node:test";
import { expect } from "./expect.ts";

import { readThemes, resolveVar } from "../src/cssVars.ts";

const CSS = `
:root { --ink: var(--base); --base: #111; --page: #fff; /* --ghost: red; */ }
[data-theme="dark"] { --base: #eee; --page: #000; }
:root .card { --ink: hotpink; }
`;

describe("resolveVar", () => {
  const themes = readThemes(CSS);

  it("follows a base-only alias into the theme that rebinds what it points at", () => {
    expect(resolveVar("--ink", "light", themes)).toBe("#111");
    expect(resolveVar("--ink", "dark", themes)).toBe("#eee");
  });

  it("reads neither comments nor descendant selectors", () => {
    expect(resolveVar("--ghost", "light", themes)).toBeNull();
    expect(resolveVar("--ink", "light", themes)).toBe("#111");
  });

  it("returns null for a cycle or an undeclared var, and takes a var() fallback", () => {
    const loop = readThemes(":root { --a: var(--b); --b: var(--a); --c: var(--nope, #123); }");
    expect(resolveVar("--a", "light", loop)).toBeNull();
    expect(resolveVar("--c", "light", loop)).toBe("#123");
  });

  it("takes the caller's theme selectors, the first being the base", () => {
    const custom = readThemes(".day { --x: #fff; } .night { --y: #000; }", { day: [".day"], night: [".night"] });
    expect(resolveVar("--x", "night", custom)).toBe("#fff");
    expect(() => resolveVar("--x", "dusk", custom)).toThrow(/no theme named "dusk"/);
  });
});
