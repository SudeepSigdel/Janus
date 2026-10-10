"""Surface perturbations of the replica sites (docs/AUDIT.md §4, generalization).

A perturbation changes only what an agent *perceives*, never what the site *does*:

- reworded visible labels (synonyms), with the en/ne order swapped;
- renamed form controls (`name=` attributes; renamed back on the way in, so the
  handlers and state are untouched);
- form fields reordered (label/control groups reversed inside each form);
- an altered layout (the nav moves below the content and a footer help link is added).

DOM `id`s and `href`s are deliberately unchanged: the oracle drives the sites by id/href,
and no agent under test ever sees either (they are not part of `Element`/`Fingerprint`).
It is applied by an ASGI middleware that is a no-op unless the site's state carries a
`perturbation` (set via `/__bench/reset`), so every v1 page stays byte-identical.

`perturb_task` rewrites a task's approval names and sensitive field names through the same
maps: a task author writes those against the deployed site's labels, so a perturbed
deployment gets a correspondingly perturbed task, not a broken one.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode

from janus.policy import ApprovalTarget


@dataclass(frozen=True)
class Perturbation:
    labels: dict[str, str]
    names: dict[str, str]
    prefixes: dict[str, str] = field(default_factory=dict)
    reorder_fields: bool = True
    nav_to_footer: bool = True


_LABELS = {
    # nagarpalika
    "पूरा नाम (देवनागरी) / Full name (Devanagari)": (
        "Applicant's name in Devanagari / निवेदकको नाम (देवनागरीमा)"
    ),
    "जन्म मिति (वि.सं.) / Date of birth (BS)": "Birth date (Bikram Sambat) / जन्म तिथि (बि.सं.)",
    "नागरिकता प्रमाणपत्र नं. / Citizenship certificate no.": "Citizenship number / नागरिकता नम्बर",
    "वडा नं. / Ward no.": "Ward / वडा",
    "मोबाइल नं. / Mobile number": "Contact phone / सम्पर्क फोन",
    "अर्को / Next: review": "Continue to review / समीक्षामा जानुहोस्",
    "पेश गर्नुहोस् / Submit application": "Confirm and submit / पुष्टि गरी पेश गर्नुहोस्",
    "सम्पादन / Edit": "Change / परिवर्तन",
    "रद्द / Cancel": "Cancel request / निवेदन रद्द",
    "रद्द गर्नुहोस् / Confirm cancel": "Yes, cancel it / हो, रद्द गर्नुहोस्",
    "सुरक्षित गर्नुहोस् / Save": "Update / अद्यावधिक गर्नुहोस्",
    "फर्कनुहोस् / Back": "Go back / पछाडि जानुहोस्",
    "सेवाहरू / Services": "All services / सबै सेवा",
    "मेरा निवेदन / My Applications": "Submitted requests / पेश गरिएका निवेदन",
    "मेरा निवेदन / My applications": "Your requests / तपाईंका निवेदन",
    "उपलब्ध सेवाहरू / Available services": "Services offered / उपलब्ध सेवा",
    "समीक्षा गर्नुहोस् / Review your application": "Check the details / विवरण जाँच्नुहोस्",
    "अर्को पृष्ठ / Next page": "More / थप",
    "-- छान्नुहोस् / Select --": "-- Choose -- / -- रोज्नुहोस् --",
    "निवास सिफारिस / Residence Recommendation": "Recommendation of residence / बसोबास सिफारिस",
    "जन्म दर्ता / Birth Registration": "Registering a birth / जन्मको दर्ता",
    "नाता प्रमाणित / Relationship Certificate": "Proof of relationship / नाता प्रमाणपत्र",
    "मेरा निवेदन हेर्नुहोस् / View my applications": "See your requests / निवेदनहरू हेर्नुहोस्",
    # sharesewa
    "वीओआईडी / BOID": "Demat BOID number / डिम्याट बीओआईडी",
    "पासवर्ड / Password": "Secret password / गोप्य शब्द",
    "लगइन / Login": "Sign in / साइन इन",
    "बैंक / Bank": "Bank account at / बैंक खाता",
    "कित्ता संख्या / Kitta": "Number of units applied / आवेदन कित्ता",
    "सीआरएन / CRN": "CRN code / सीआरएन कोड",
    "ट्रान्जेक्सन पिन / Transaction PIN": "Security PIN / सुरक्षा पिन",
    "आवेदन पेश गर्नुहोस् / Submit application": "Confirm and submit / पुष्टि गरी पेश गर्नुहोस्",
    "पुष्टि गर्नुहोस् / Confirm withdraw": "Yes, confirm withdrawal / हो, पुष्टि गर्नुहोस्",
    "फिर्ता / Withdraw": "Take back / फिर्ता लिनुहोस्",
    "खुल्ला निष्कासन / Open Issues": "Issues open now / हाल खुला निष्कासन",
    "खुल्ला निष्कासन / Open issues": "Currently open / हाल खुला",
    "मेरो रिपोर्ट / My Report": "Application history / आवेदन इतिहास",
    "मेरो रिपोर्ट हेर्नुहोस् / View my report": "See application history / आवेदन इतिहास हेर्नुहोस्",
    "मेरो आवेदन रिपोर्ट / My application report": "Your application history / तपाईंको आवेदन इतिहास",
}

_NAMES_NAG = {
    "name_ne": "applicant_name",
    "dob_bs": "birth_date",
    "citizenship_no": "ctz_number",
    "ward": "ward_no",
    "phone": "contact_mobile",
}
_NAMES_SHARE = {
    "boid": "demat_id",
    "password": "secret",
    "bank": "bank_code",
    "kitta": "units",
    "crn": "crn_code",
    "pin": "sec_pin",
}

PERTURBATIONS: dict[str, Perturbation] = {
    # p1: every surface change at once (labels + names + order + layout).
    "p1": Perturbation(
        labels=_LABELS,
        names={**_NAMES_NAG, **_NAMES_SHARE},
        prefixes={"आवेदन / Apply — ": "Apply now: "},
    ),
}


# --- HTML rewriting -------------------------------------------------------------------------

_TEXT_NODE = re.compile(r">(\s*)([^<>]+?)(\s*)<")
_NAME_ATTR = re.compile(r'\bname="([^"]+)"')
_FORM = re.compile(r"(<form\b[^>]*>)(.*?)(</form>)", re.DOTALL)
_GROUP = re.compile(
    r'<label for="[^"]+">.*?</label>\s*(?:<input\b[^>]*>|<select\b.*?</select>)'
    r'\s*(?:<div class="error">.*?</div>)?',
    re.DOTALL,
)
_NAV = re.compile(r"\s*<nav>.*?</nav>", re.DOTALL)
_TITLE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_FOOTER_LINK = '<p><a href="#help" id="pert-help">Help / सहायता</a></p>'


def _relabel(text: str, p: Perturbation) -> str:
    if text in p.labels:
        return p.labels[text]
    for old, new in p.prefixes.items():
        if text.startswith(old):
            return new + text[len(old) :]
    return text


def _reorder(form_inner: str) -> str:
    groups = list(_GROUP.finditer(form_inner))
    if len(groups) < 2:
        return form_inner
    start, end = groups[0].start(), groups[-1].end()
    reordered = "\n  ".join(m.group(0) for m in reversed(groups))
    return form_inner[:start] + reordered + form_inner[end:]


def rewrite_html(html: str, p: Perturbation) -> str:
    def text_node(m: re.Match[str]) -> str:
        return f">{m.group(1)}{_relabel(m.group(2), p)}{m.group(3)}<"

    def title(m: re.Match[str]) -> str:
        parts = [_relabel(part.strip(), p) for part in m.group(1).split(" | ")]
        return f"<title>{' | '.join(parts)}</title>"

    html = _TITLE.sub(title, html)
    html = _TEXT_NODE.sub(text_node, html)
    html = _NAME_ATTR.sub(lambda m: f'name="{p.names.get(m.group(1), m.group(1))}"', html)
    if p.reorder_fields:
        html = _FORM.sub(lambda m: m.group(1) + _reorder(m.group(2)) + m.group(3), html)
    if p.nav_to_footer:
        nav = _NAV.search(html)
        if nav is not None and "</main>" in html:
            html = html[: nav.start()] + html[nav.end() :]
            html = html.replace(
                "</main>", f"</main>\n  {nav.group(0).strip()}\n  {_FOOTER_LINK}", 1
            )
    return html


def unrename_form(body: bytes, p: Perturbation) -> bytes:
    reverse = {new: old for old, new in p.names.items()}
    pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True)
    return urlencode([(reverse.get(k, k), v) for k, v in pairs]).encode("utf-8")


# --- ASGI middleware ------------------------------------------------------------------------

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class PerturbMiddleware:
    """No-op unless `current()` names a perturbation in `PERTURBATIONS`."""

    def __init__(self, app: Any, current: Callable[[], str | None]) -> None:
        self.app = app
        self.current = current

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        name = self.current() if scope["type"] == "http" else None
        p = PERTURBATIONS.get(name) if name else None
        if p is None or scope["path"].startswith("/__bench"):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        if headers.get(b"content-type", b"").startswith(b"application/x-www-form-urlencoded"):
            chunks = []
            while True:
                message = await receive()
                chunks.append(message.get("body", b""))
                if not message.get("more_body"):
                    break
            body = unrename_form(b"".join(chunks), p)
            sent = False

            async def receive_rewritten() -> Message:
                nonlocal sent
                if sent:
                    return {"type": "http.disconnect"}
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}

            fixed = [(k, v) for k, v in scope["headers"] if k != b"content-length"]
            scope = {**scope, "headers": [*fixed, (b"content-length", str(len(body)).encode())]}
            receive = receive_rewritten

        start: Message | None = None
        parts: list[bytes] = []

        async def send_rewritten(message: Message) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                start = message
                return
            if message["type"] != "http.response.body" or start is None:
                await send(message)
                return
            parts.append(message.get("body", b""))
            if message.get("more_body"):
                return
            body = b"".join(parts)
            resp_headers = [(k, v) for k, v in start.get("headers", [])]
            ctype = dict(resp_headers).get(b"content-type", b"")
            if ctype.startswith(b"text/html"):
                body = rewrite_html(body.decode("utf-8"), p).encode("utf-8")
                resp_headers = [(k, v) for k, v in resp_headers if k != b"content-length"]
                resp_headers.append((b"content-length", str(len(body)).encode()))
            await send({**start, "headers": resp_headers})
            await send({"type": "http.response.body", "body": body, "more_body": False})

        await self.app(scope, receive, send_rewritten)


# --- task rewriting -------------------------------------------------------------------------


def perturb_names(names: list[str], p: Perturbation) -> list[str]:
    return [_relabel(n, p) for n in names]


def perturb_approval(approval: ApprovalTarget, p: Perturbation) -> ApprovalTarget:
    return approval.model_copy(update={"names": perturb_names(approval.names, p)})


def perturbed_sensitive_fields(fields: list[str], p: Perturbation) -> list[str]:
    return [p.names.get(f, f) for f in fields]
