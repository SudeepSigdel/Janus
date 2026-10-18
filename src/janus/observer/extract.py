"""Bounded page extraction: build a PageSnapshot from a live Playwright page.

One `page.evaluate()` pass collects raw structural elements (interactive controls)
and raw text blocks (everything else visible). Caps/truncation are applied in pure
Python below so that logic is unit-testable without a browser.
"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import CDPSession, ElementHandle, Page

from janus.config import Settings, get_settings
from janus.observer.fingerprint import compute_fingerprint
from janus.observer.snapshot import Element, PageSnapshot, SelectOption
from janus.text.nepali import normalize_row_key

# Kept in sync with the identical literal inside `_JS` below by the golden-snapshot
# tests (any drift changes what `extract_snapshot` returns). Exported so M4's executor
# can re-select an element by its position in this same DOM-order, visibility-filtered
# list for act-time fingerprint re-resolution (security invariant 5).
INTERACTIVE_SELECTOR = 'a[href], button, input:not([type="hidden"]), select, textarea, [role]'

# Shared by the snapshot and act-time re-resolution so both walk the same ordered list.
# Besides semantic controls it picks up "click targets": a span/div/li/img whose cursor is
# `pointer` while its parent's is not, with no semantic control inside or around it. Real pages
# build menus, tabs and cards this way; they get role `button` and a name from their text.
_COLLECT_JS = r"""
const interactiveSelector =
  'a[href], button, input:not([type="hidden"]), select, textarea, [role]';
const clickTargetTags = 'span, div, li, img, td, p';
const clickTargets = new WeakSet();
function isVisible(el) {
  const style = window.getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden') return false;
  if (typeof el.checkVisibility === 'function') {
    return el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
  }
  return true;
}
function isClickTarget(el) {
  if (el.matches(interactiveSelector)) return false;
  if (window.getComputedStyle(el).cursor !== 'pointer') return false;
  const parent = el.parentElement;
  if (!parent || window.getComputedStyle(parent).cursor === 'pointer') return false;
  if (el.querySelector(interactiveSelector) || el.parentElement.closest(interactiveSelector)) {
    return false;
  }
  return (el.textContent || '').trim() !== '' || el.getAttribute('alt') || el.getAttribute('title');
}
function collectInteractive() {
  const all = document.querySelectorAll(interactiveSelector + ', ' + clickTargetTags);
  const out = [];
  for (const el of all) {
    if (!isVisible(el)) continue;
    if (el.matches(interactiveSelector)) { out.push(el); continue; }
    if (isClickTarget(el)) { clickTargets.add(el); out.push(el); }
  }
  return out;
}
// D5: a click target may hold several separately clickable children (Reply and Forward inside
// one pointer container). Candidates are its visible descendants with text; Python checks their
// click listeners over CDP and returns which to emit instead of the container.
const MAX_SPLIT_CONTAINERS = 20;
const MAX_SPLIT_KIDS = 8;
const squashText = (t) => (t || '').replace(/\s+/g, ' ').trim();
function splitCandidates(list) {
  const found = [];
  list.forEach((el, i) => {
    if (!clickTargets.has(el) || found.length >= MAX_SPLIT_CONTAINERS) return;
    const kids = [];
    for (const d of el.querySelectorAll('*')) {
      if (kids.length >= MAX_SPLIT_KIDS) break;
      if (!isVisible(d) || !squashText(d.textContent)) continue;
      kids.push(d);
    }
    if (kids.length >= 2) found.push([i, el, kids]);
  });
  window.__janusSplit = found;
  return found.map(([i, el, kids]) => ({
    index: i,
    parents: kids.map((k) => kids.findIndex((other) => other !== k && other.contains(k))),
  }));
}
function applySplit(list, decisions) {
  const stored = window.__janusSplit || [];
  const out = [];
  list.forEach((el, i) => {
    const chosen = decisions[String(i)];
    const entry = stored.find((c) => c[0] === i && c[1] === el);
    if (!chosen || !entry) { out.push(el); return; }
    for (const k of chosen) {
      const kid = entry[2][k];
      if (!kid) continue;
      clickTargets.add(kid);
      out.push(kid);
    }
  });
  return out;
}
"""

# Interactive elements become trusted, short-labelled Elements. Everything else
# visible (headings, paragraphs, notices, table cells, review values) is untrusted
# text: the planner never sees it (CLAUDE.md: page text is data, never instructions).
_JS = (
    "(decisions) => {"
    + _COLLECT_JS
    + r"""
  const textSelector = 'h1, h2, h3, h4, h5, h6, p, dt, dd, li, td, th, .notice';

  function roleOf(el) {
    if (clickTargets.has(el)) return 'button';
    const explicit = el.getAttribute('role');
    // A spinbutton or searchbox is a text field for the planner's purposes.
    if (explicit === 'spinbutton' || explicit === 'searchbox') return 'textbox';
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
    if (clickTargets.has(el)) {
      return squash(el.textContent) || squash(el.getAttribute('alt'))
        || squash(el.getAttribute('title'));
    }
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

  // B4a: a link to the page that is already open (aria-current, or an href equal
  // to the current URL ignoring the fragment). Hash-only and javascript: hrefs are
  // excluded: those usually open a menu or run a handler, so clicking them can matter.
  const isCurrentPageLink = (el) => {
    if (el.tagName.toLowerCase() !== 'a') return false;
    const ac = el.getAttribute('aria-current');
    if (ac && ac !== 'false') return true;
    const raw = (el.getAttribute('href') || '').trim();
    if (!raw || raw.startsWith('#') || raw.toLowerCase().startsWith('javascript:')) return false;
    try {
      const u = new URL(el.href); const here = new URL(location.href);
      u.hash = ''; here.hash = '';
      return u.href === here.href;
    } catch (e) { return false; }
  };

  const interactive = applySplit(collectInteractive(), decisions || {});
  const elements = interactive.map((el) => {
    const form = el.closest('form');
    return {
      tag: el.tagName.toLowerCase(),
      role: roleOf(el),
      accessible_name: labelFor(el),
      name_attr: el.getAttribute('name'),
      form_id: form ? form.getAttribute('id') : null,
      row_cell: firstCellText(el),
      current_page: isCurrentPageLink(el),
      input_type: el.tagName.toLowerCase() === 'input'
        ? (el.getAttribute('type') || 'text').toLowerCase() : null,
      autocomplete: (el.getAttribute('autocomplete') || '').toLowerCase() || null,
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

  // Own text of every visible element (divs and spans too), for quarantined reads only.
  const readBlocks = [];
  for (const el of document.body ? document.body.querySelectorAll('*') : []) {
    if (['script', 'style', 'noscript'].includes(el.tagName.toLowerCase())) continue;
    let own = '';
    for (const node of el.childNodes) if (node.nodeType === 3) own += node.textContent;
    own = own.replace(/\s+/g, ' ').trim();
    if (own && isVisible(el)) readBlocks.push(own);
  }

  return { elements, text_blocks: blocks, read_text: readBlocks };
}
"""
)


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
    raw = page.evaluate(_JS, split_decisions(page))
    raw_elements: list[dict[str, Any]] = raw["elements"]
    raw_text_blocks: list[str] = raw["text_blocks"]
    read_text, _ = cap_untrusted_text(
        raw.get("read_text", []), settings.observer_max_untrusted_chars
    )

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
                current_page=bool(raw_el.get("current_page", False)),
                input_type=raw_el.get("input_type"),
                autocomplete=raw_el.get("autocomplete"),
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
        read_text=read_text,
    )


_CANDIDATES_JS = "() => {" + _COLLECT_JS + "return splitCandidates(collectInteractive()); }"


def split_children(
    container_has_click: bool, kid_has_click: list[bool], kid_parent: list[int]
) -> list[int]:
    """D5's rule: which of a click target's candidate children to emit instead of it.

    Split only when the container has no click listener of its own and at least two visible,
    named descendants each have one. Of those, keep the outermost: a clickable child inside
    another clickable child is part of it. Anything else keeps today's single control.
    """
    if container_has_click:
        return []
    clickable = {k for k, has in enumerate(kid_has_click) if has}

    def nested(k: int) -> bool:
        parent = kid_parent[k]
        while parent != -1:
            if parent in clickable:
                return True
            parent = kid_parent[parent]
        return False

    chosen = sorted(k for k in clickable if not nested(k))
    return chosen if len(chosen) >= 2 else []


def _has_click(cdp: CDPSession, expression: str) -> bool:
    found = cdp.send("Runtime.evaluate", {"expression": expression})
    object_id = found.get("result", {}).get("objectId")
    if not object_id:
        raise RuntimeError("element not found")
    listeners = cdp.send("DOMDebugger.getEventListeners", {"objectId": object_id, "depth": 0})
    return any(item.get("type") == "click" for item in listeners.get("listeners", []))


def split_decisions(page: Page) -> dict[str, list[int]]:
    """{click-target index: child indices to emit instead}, from real click listeners.

    Best effort: any failure (no CDP outside Chromium, a page that changed mid-check) means no
    split, which is exactly the observer's behaviour before D5.
    """
    try:
        candidates = page.evaluate(_CANDIDATES_JS)
    except Exception:  # noqa: BLE001
        return {}
    if not candidates:
        return {}
    decisions: dict[str, list[int]] = {}
    try:
        cdp = page.context.new_cdp_session(page)
    except Exception:  # noqa: BLE001
        return {}
    try:
        for j, candidate in enumerate(candidates):
            base = f"window.__janusSplit[{j}]"
            container = _has_click(cdp, f"{base}[1]")
            kids = [_has_click(cdp, f"{base}[2][{k}]") for k in range(len(candidate["parents"]))]
            chosen = split_children(container, kids, candidate["parents"])
            if chosen:
                decisions[str(candidate["index"])] = chosen
    except Exception:  # noqa: BLE001
        return {}
    finally:
        try:
            cdp.detach()
        except Exception:  # noqa: BLE001
            pass
    return decisions


def extract_raw_elements(page: Page) -> list[dict[str, Any]]:
    """Uncapped element descriptors, in the same DOM order used to assign refs.

    Used for act-time re-resolution (executor/resolve.py): recomputing fingerprints
    fresh, right before acting, without the element/label caps that only apply to
    what the planner is allowed to see.
    """
    return page.evaluate(_JS, split_decisions(page))["elements"]


_HANDLE_AT_INDEX_JS = (
    "(args) => {"
    + _COLLECT_JS
    + """
  const index = args[1];
  return applySplit(collectInteractive(), args[2] || {})[index] ?? null;
}"""
)


def handle_at_index(page: Page, index: int) -> ElementHandle | None:
    """The live element at `index` in the same ordering `extract_raw_elements` uses.

    None if `index` is out of range (the page has fewer matching elements now than
    when it was last observed).
    """
    handle = page.evaluate_handle(
        _HANDLE_AT_INDEX_JS, [INTERACTIVE_SELECTOR, index, split_decisions(page)]
    )
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
