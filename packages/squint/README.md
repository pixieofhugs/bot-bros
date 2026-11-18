# @pixieofhugs/squint

WCAG contrast that fails loudly. Unreadable colours are failures, never skips.

- `parseColor`, `contrastRatio`, `compositeOver`, `deltaE76`, `requiredRatio`: the maths.
- `readThemes`, `resolveVar`: CSS custom properties per theme, through every `var()` hop.
- `scanPageForContrast`: run it in a page with Playwright's `page.evaluate`; it measures every
  visible text node against the backdrop it sits on.
- `triageFindings`: what those findings mean against a baseline that only shrinks.

How to use them, and why each rule exists: the `squint` skill in
[bot-bros](https://github.com/pixieofhugs/bot-bros/tree/main/plugins/squint/skills/squint/SKILL.md).

Publishing (the owner): `cd packages/squint && npm publish --access public`, which builds and
tests first.
