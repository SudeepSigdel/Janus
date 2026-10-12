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
from janus.text.nepali import normalize_row_key

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

  // B1c (docs/AUDIT.md S12): checkVisibility also hides elements inside a hidden ancestor
  // (a closed dropdown menu) and elements made invisible with opacity 0.
  function isVisible(el) {
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    if (typeof el.checkVisibility === 'function') {
      return el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
    }
    return true;
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

  // ARIA roles whose accessible name comes from their content (WAI-ARIA "name from
  // content"). B1: MiniWoB's tabs, options and tree items had empty names without this.
  const nameFromContent = new Set([
    'button', 'cell', 'checkbox', 'columnheader', 'gridcell', 'heading', 'link', 'menuitem',
    'menuitemcheckbox', 'menuitemradio', 'option', 'radio', 'row', 'rowheader', 'switch',
    'tab', 'tooltip', 'treeitem',
  ]);

  function squash(text) {
    return (text || '').replace(/\s+/g, ' ').trim();
  }

  // Text of `container` without the text of any interactive control inside it.
  function ownText(container) {
    const clone = container.cloneNode(true);
    clone.querySelectorAll(interactiveSelector).forEach((n) => n.remove());
    return squash(clone.textContent);
  }

  function labelFor(el) {
    const id = el.getAttribute('id');
    if (id) {
      const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (label && label.textContent.trim()) return label.textContent.trim().replace(/\s+/g, ' ');
    }
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();
    // B1 fallbacks below only name elements that would otherwise have no name.
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const text = squash(labelledBy.split(/\s+/)
        .map((ref) => document.getElementById(ref))
        .filter((n) => n)
        .map((n) => n.textContent)
        .join(' '));
      if (text) return text;
    }
    const wrapping = el.closest('label');
    if (wrapping) {
      const text = ownText(wrapping);
      if (text) return text;
    }
    const placeholder = el.getAttribute('placeholder');
    if (placeholder && placeholder.trim()) return placeholder.trim();
    const tag = el.tagName.toLowerCase();
    if (tag === 'a' || tag === 'button') {
      const text = el.textContent.trim().replace(/\s+/g, ' ');
      if (text) return text;
      // B1d: an icon-only link/button is named by its image's alt text, else falls
      // through to title below (the forum's upvote button: title="Upvote", icon only).
      const alt = squash(Array.from(el.querySelectorAll('img[alt]'))
        .map((i) => i.getAttribute('alt')).join(' '));
      if (alt) return alt;
    }
    if (tag === 'input') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (['submit', 'button', 'reset'].includes(type)) {
        const value = squash(el.getAttribute('value'));
        if (value) return value;
      }
    }
    const role = el.getAttribute('role');
    if (role && nameFromContent.has(role)) {
      const text = squash(el.textContent);
      if (text) return text;
    }
    const title = squash(el.getAttribute('title'));
    if (title) return title;
    // Last resort for an unlabelled form control: its name/id attribute, humanized
    // (name="user_name" -> "user name"). Only for controls, never links/buttons.
    if (['input', 'select', 'textarea'].includes(tag)) {
      const attr = el.getAttribute('name') || el.getAttribute('id') || '';
      return squash(attr.replace(/[_\-]+/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2'));
    }
    return '';
  }

  // Raw (unnormalized) text of the enclosing table row's first cell, if any --
  // docs/PLAN.md P5. Python normalizes/validates this (text/nepali.py::normalize_row_key)
  // before it becomes a trusted `row_key`; the JS side only locates the cell.
  function firstCellText(el) {
    const row = el.closest('tr');
    if (!row) return null;
    const cell = row.querySelector('td, th');
    if (!cell) return null;
    return cell.textContent.replace(/\s+/g, ' ').trim();
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
      row_cell: firstCellText(el),
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
        row_key = normalize_row_key(raw_el.get("row_cell"))
        fingerprint = compute_fingerprint(
            role=raw_el["role"],
            accessible_name=accessible_name,
            name_attr=raw_el["name_attr"],
            form_id=raw_el["form_id"],
            tag=raw_el["tag"],
            row_key=row_key,
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
                row_key=row_key,
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


# Must match `isVisible` inside `_JS` (act-time re-resolution walks the same list).
_IS_VISIBLE_JS = """
function isVisible(el) {
  const style = window.getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  if (typeof el.checkVisibility === 'function') {
    return el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
  }
  return true;
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
