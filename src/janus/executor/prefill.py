"""Untouched pre-filled fields (docs/LIMITATIONS.md, Q5): a form can open with a value already in
an optional text field the task never mentions, and a typed plan never looks at fields it does not
touch. Before a click that submits a form, this finds those fields in the same form so the run can
disclose them to the human, or, when no human is present and the policy says so, clear them.

It looks at the optional fields of the form being submitted: text fields that hold a value,
selects whose page-chosen option is not the first one, and checkboxes that arrived ticked.
Clearing empties a text field, resets a select to its empty placeholder option (if it has one)
and unticks a box.
Required fields, passwords and hidden inputs are left alone, and nothing here reaches a model.
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
  const labelOf = (c) => {
    const id = c.getAttribute('id');
    const lab = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
    const wrap = c.closest('label');
    return ((lab && lab.textContent) || (wrap && wrap.textContent) || c.getAttribute('aria-label')
      || c.getAttribute('placeholder') || c.getAttribute('name') || '').replace(/\s+/g, ' ').trim();
  };
  const fire = (c) => {
    c.dispatchEvent(new Event('input', { bubbles: true }));
    c.dispatchEvent(new Event('change', { bubbles: true }));
  };
  for (const c of Array.from(form.elements)) {
    const tag = c.tagName.toLowerCase();
    if (c.disabled || c.required) continue;
    const label = labelOf(c);
    const name = c.getAttribute('name') || '';
    if ((name && skip.has(name)) || (label && skip.has(label))) continue;
    const type = (c.getAttribute('type') || 'text').toLowerCase();
    if (tag === 'select') {
      // A non-first option the page marked as selected, which the run never chose.
      const i = c.selectedIndex;
      if (i <= 0 || !c.options[i].defaultSelected || c.options[i].value === '') continue;
      found.push({ label, value: c.options[i].textContent.trim(), kind: 'select' });
      if (args.clear && c.options[0].value === '') { c.selectedIndex = 0; fire(c); }
      continue;
    }
    if (tag === 'input' && type === 'checkbox') {
      // A box that arrived ticked (a consent or opt-in the user never chose).
      if (!c.checked || !c.defaultChecked) continue;
      found.push({ label, value: 'ticked', kind: 'checkbox' });
      if (args.clear) { c.checked = false; fire(c); }
      continue;
    }
    if (tag !== 'input' && tag !== 'textarea') continue;
    if (tag === 'input' && !textTypes.includes(type)) continue;
    if (c.readOnly) continue;
    const value = (c.value || '').trim();
    if (!value) continue;
    found.push({ label, value, kind: 'text' });
    if (args.clear) { c.value = ''; fire(c); }
  }
  return found;
}
"""


def _run(handle: ElementHandle, filled: Iterable[str], clear: bool) -> list[dict[str, str]]:
    found = handle.evaluate(_JS, {"filled": sorted(set(filled)), "clear": clear})
    return [
        {"label": item["label"], "value": item["value"][:MAX_VALUE_CHARS], "kind": item["kind"]}
        for item in found
    ]


def scan_untouched_prefill(handle: ElementHandle, filled: Iterable[str]) -> list[dict[str, str]]:
    """Optional fields in `handle`'s form holding a value this run did not set."""
    return _run(handle, filled, clear=False)


def clear_untouched_prefill(handle: ElementHandle, filled: Iterable[str]) -> list[dict[str, str]]:
    """Empty those fields (firing input/change) and return what was removed."""
    return _run(handle, filled, clear=True)
