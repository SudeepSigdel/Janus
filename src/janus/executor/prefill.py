"""Untouched pre-filled fields (docs/LIMITATIONS.md, Q5): a form can open with a value already in
an optional text field the task never mentions, and a typed plan never looks at fields it does not
touch. Before a click that submits a form, this finds those fields in the same form so the run can
disclose them to the human, or, when no human is present and the policy says so, clear them.

It only reads and clears optional, editable text fields of the target's own form. Required fields,
selects, checkboxes, passwords and hidden inputs are left alone, and nothing here reaches a model.
"""

from __future__ import annotations

from collections.abc import Iterable

from playwright.sync_api import ElementHandle

MAX_VALUE_CHARS = 60

_JS = r"""
(el, args) => {
  const form = el.form || el.closest('form');
  if (!form) return [];
  // Only a click that submits the form: a type=button control or a link leaves it alone.
  const kind = (el.getAttribute('type') || (el.tagName === 'BUTTON' ? 'submit' : '')).toLowerCase();
  const submits = (el.tagName === 'BUTTON' && kind === 'submit')
    || (el.tagName === 'INPUT' && (kind === 'submit' || kind === 'image'));
  if (!submits) return [];
  const skip = new Set(args.filled);
  const textTypes = ['text', 'search', 'tel', 'email', 'url', 'number'];
  const found = [];
  for (const c of Array.from(form.elements)) {
    const tag = c.tagName.toLowerCase();
    if (tag !== 'input' && tag !== 'textarea') continue;
    if (tag === 'input' && !textTypes.includes((c.getAttribute('type') || 'text').toLowerCase())) {
      continue;
    }
    if (c.readOnly || c.disabled || c.required) continue;
    const value = (c.value || '').trim();
    if (!value) continue;
    const id = c.getAttribute('id');
    const lab = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
    const label = ((lab && lab.textContent) || c.getAttribute('aria-label')
      || c.getAttribute('placeholder') || c.getAttribute('name') || '').replace(/\s+/g, ' ').trim();
    const name = c.getAttribute('name') || '';
    if ((name && skip.has(name)) || (label && skip.has(label))) continue;
    found.push({ label, value });
    if (args.clear) {
      c.value = '';
      c.dispatchEvent(new Event('input', { bubbles: true }));
      c.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }
  return found;
}
"""


def _run(handle: ElementHandle, filled: Iterable[str], clear: bool) -> list[dict[str, str]]:
    found = handle.evaluate(_JS, {"filled": sorted(set(filled)), "clear": clear})
    return [{"label": item["label"], "value": item["value"][:MAX_VALUE_CHARS]} for item in found]


def scan_untouched_prefill(handle: ElementHandle, filled: Iterable[str]) -> list[dict[str, str]]:
    """Optional text fields in `handle`'s form that hold a value this run did not set."""
    return _run(handle, filled, clear=False)


def clear_untouched_prefill(handle: ElementHandle, filled: Iterable[str]) -> list[dict[str, str]]:
    """Empty those fields (firing input/change) and return what was removed."""
    return _run(handle, filled, clear=True)
