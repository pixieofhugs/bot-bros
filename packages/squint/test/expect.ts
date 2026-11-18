import assert from "node:assert/strict";
import { it } from "node:test";

/** The handful of vitest matchers these tests use, over node:assert. */
export function expect(actual: any) {
  return {
    toBe: (expected: unknown) => assert.equal(actual, expected),
    toEqual: (expected: unknown) => assert.deepEqual(actual, expected),
    toBeNull: () => assert.equal(actual, null),
    toHaveLength: (length: number) => assert.equal(actual.length, length),
    toContain: (item: unknown) => assert.ok(actual.includes(item), `${actual} lacks ${item}`),
    toBeGreaterThan: (floor: number) => assert.ok(actual > floor, `${actual} <= ${floor}`),
    toBeLessThan: (ceiling: number) => assert.ok(actual < ceiling, `${actual} >= ${ceiling}`),
    toBeCloseTo: (expected: number, digits = 2) =>
      assert.ok(Math.abs(actual - expected) < 10 ** -digits / 2, `${actual} is not ~${expected}`),
    toThrow: (pattern?: RegExp) => assert.throws(actual, pattern),
    not: { toBeNull: () => assert.notEqual(actual, null) },
  };
}

/** vitest's `it.each`: `%s` in the name takes each row's first value. */
export function each<Row>(rows: Row[]) {
  return (name: string, body: (...args: any[]) => void) => {
    for (const row of rows) {
      const args = Array.isArray(row) ? row : [row];
      it(name.replace("%s", String(args[0])), () => body(...args));
    }
  };
}
