"""Bounded page extraction: build a PageSnapshot from a live Playwright page.

One `page.evaluate()` pass collects raw structural elements (interactive controls)
and raw text blocks (everything else visible). Caps/truncation are applied in pure
Python below so that logic is unit-testable without a browser.
"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import ElementHandle, Page

from janus.config import Settings, get_settings
from janus.observer.fingerprint import compute_fingerprint
from janus.observer.snapshot import Element, PageSnapshot, SelectOption

# Kept in sync with the identical literal inside `_JS` below by the golden-snapshot
# tests (any drift changes what `extract_snapshot` returns). Exported so M4's executor
# can re-select an element by its position in this same DOM-order, visibility-filtered
# list for act-time fingerprint re-resolution (security invariant 5).
INTERACTIVE_SELECTOR = 'a[href], button, input:not([type="hidden"]), select, textarea, [role]'

# Interactive elements become trusted, short-labelled Elements. Everything else
# visible (headings, paragraphs, notices, table cells, review values) is untrusted
# text: the planner never sees it (CLAUDE.md: page text is data, never instructions).
_JS = r"""
() => {
  const interactiveSelector =
    'a[href], button, input:not([type="hidden"]), select, textarea, [role]';
  const textSelector = 'h1, h2, h3, h4, h5, h6, p, dt, dd, li, td, th, .notice';

  function isVisible(el) {
    const style = window.getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden';
  }

  function roleOf(el) {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (type === 'submit' || type === 'button') return 'button';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      return 'textbox';
    }
    return 'generic';
  }

  function labelFor(el) {
    const id = el.getAttribute('id');
    if (id) {
      const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (label && label.textContent.trim()) return label.textContent.trim().replace(/\s+/g, ' ');
    }
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();
    const placeholder = el.getAttribute('placeholder');
    if (placeholder && placeholder.trim()) return placeholder.trim();
    const tag = el.tagName.toLowerCase();
    if (tag === 'a' || tag === 'button') return el.textContent.trim().replace(/\s+/g, ' ');
    return '';
  }

  const interactive = Array.from(document.querySelectorAll(interactiveSelector)).filter(isVisible);
  const elements = interactive.map((el) => {
    const form = el.closest('form');
    return {
      tag: el.tagName.toLowerCase(),
      role: roleOf(el),
      accessible_name: labelFor(el),
      name_attr: el.getAttribute('name'),
      form_id: form ? form.getAttribute('id') : null,
    };
  });

  // Text blocks: a block's own text with any interactive descendant removed, so an
  // <li> or <td> that only wraps a link/button contributes nothing (already captured
  // above) while its surrounding label text is still kept.
  const blocks = [];
  for (const el of document.querySelectorAll(textSelector)) {
    if (!isVisible(el)) continue;
    const clone = el.cloneNode(true);
    clone.querySelectorAll(interactiveSelector).forEach((n) => n.remove());
    const text = clone.textContent.replace(/\s+/g, ' ').trim();
    if (text) blocks.push(text);
  }

  return { elements, text_blocks: blocks };
}
"""


def cap_elements(
    raw_elements: list[dict[str, Any]], max_elements: int
) -> tuple[list[dict[str, Any]], bool]:
    return raw_elements[:max_elements], len(raw_elements) > max_elements


def truncate_label(label: str, max_chars: int) -> str:
    if len(label) <= max_chars:
        return label
    return label[: max_chars - 1].rstrip() + "…"


def cap_untrusted_text(blocks: list[str], max_chars: int) -> tuple[list[str], bool]:
    kept: list[str] = []
    total = 0
    truncated = False
    for block in blocks:
        remaining = max_chars - total
        if remaining <= 0:
            truncated = True
            break
        if len(block) > remaining:
            kept.append(block[:remaining].rstrip() + "…")
            truncated = True
            break
        kept.append(block)
        total += len(block)
    return kept, truncated


def extract_snapshot(page: Page, settings: Settings | None = None) -> PageSnapshot:
    settings = settings or get_settings()
    raw = page.evaluate(_JS)
    raw_elements: list[dict[str, Any]] = raw["elements"]
    raw_text_blocks: list[str] = raw["text_blocks"]

    capped_elements, elements_truncated = cap_elements(raw_elements, settings.observer_max_elements)
    elements: list[Element] = []
    for i, raw_el in enumerate(capped_elements):
        accessible_name = truncate_label(
            raw_el["accessible_name"], settings.observer_max_label_chars
        )
        fingerprint = compute_fingerprint(
            role=raw_el["role"],
            accessible_name=accessible_name,
            name_attr=raw_el["name_attr"],
            form_id=raw_el["form_id"],
            tag=raw_el["tag"],
        )
        elements.append(
            Element(
                ref=f"e{i}",
                tag=raw_el["tag"],
                role=raw_el["role"],
                accessible_name=accessible_name,
                name_attr=raw_el["name_attr"],
                form_id=raw_el["form_id"],
                fingerprint=fingerprint,
            )
        )

    untrusted_text, untrusted_text_truncated = cap_untrusted_text(
        raw_text_blocks, settings.observer_max_untrusted_chars
    )

    return PageSnapshot(
        url=page.url,
        title=page.title(),
        elements=elements,
        untrusted_text=untrusted_text,
        elements_truncated=elements_truncated,
        untrusted_text_truncated=untrusted_text_truncated,
    )


def extract_raw_elements(page: Page) -> list[dict[str, Any]]:
    """Uncapped element descriptors, in the same DOM order used to assign refs.

    Used for act-time re-resolution (executor/resolve.py): recomputing fingerprints
    fresh, right before acting, without the element/label caps that only apply to
    what the planner is allowed to see.
    """
    return page.evaluate(_JS)["elements"]


_IS_VISIBLE_JS = """
function isVisible(el) {
  const style = window.getComputedStyle(el);
  return style.display !== 'none' && style.visibility !== 'hidden';
}
"""

_HANDLE_AT_INDEX_JS = f"""
(args) => {{
  {_IS_VISIBLE_JS}
  const [selector, index] = args;
  const list = Array.from(document.querySelectorAll(selector)).filter(isVisible);
  return list[index] ?? null;
}}
"""


def handle_at_index(page: Page, index: int) -> ElementHandle | None:
    """The live element at `index` in the same ordering `extract_raw_elements` uses.

    None if `index` is out of range (the page has fewer matching elements now than
    when it was last observed).
    """
    handle = page.evaluate_handle(_HANDLE_AT_INDEX_JS, [INTERACTIVE_SELECTOR, index])
    return handle.as_element()


_SELECT_OPTIONS_JS = """
(select) => Array.from(select.options).map((o) => ({ value: o.value, label: o.textContent.trim() }))
"""


def extract_select_options(handle: ElementHandle) -> list[SelectOption]:
    """A `<select>`'s options, read live from an already-resolved handle.

    Options aren't part of the bounded `PageSnapshot` (M2's known gap) -- this is
    only ever called for a `<select>` a committed plan actually targets
    (planner/ground.py), never eagerly for every element on the page.
    """
    raw: list[dict[str, str]] = handle.evaluate(_SELECT_OPTIONS_JS)
    return [SelectOption(value=o["value"], label=o["label"]) for o in raw]
