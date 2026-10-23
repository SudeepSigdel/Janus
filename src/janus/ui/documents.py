"""Documents in `janus ui`: a photo is read into candidate values, the person confirms them, and
only then do they become task inputs (docs/PLAN.md "Photo to fill").

Nothing a model read from a photo is used until the person presses Confirm, and every confirmed
value is checked again by janus/text/fields.py, so a typo or a bad read cannot reach a form as
something the validators would reject. The photo and the values are personal data: they live in
memory only, are never traced or logged, and are wiped when used, deleted or expired.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from janus.config import Settings
from janus.extract_doc import DocField, DocumentError, extract_document
from janus.llm import LLMClient, LLMError
from janus.text.fields import normalize_field

MAX_PHOTO_BYTES = 12 * 1024 * 1024
TTL_S = 30 * 60
MAX_DOCUMENTS = 5

# What each kind of document may supply. The person picks the kind: it is the schema the model is
# asked for, and asking only for fields that exist on the document keeps it from inventing the rest.
DOCUMENT_KINDS: dict[str, dict[str, Any]] = {
    "citizenship": {
        "label": "नागरिकता प्रमाणपत्र · Citizenship certificate",
        "keys": ["name", "citizenship_no", "dob_bs", "ward"],
    },
    "demat": {
        "label": "डिम्याट खाता विवरण · Demat account slip",
        "keys": ["name", "boid", "crn", "bank"],
    },
    "id_card": {
        "label": "पहचान पत्र · ID card",
        "keys": ["name", "dob_ad", "phone"],
    },
}
FIELD_LABELS = {
    "name": "नाम · Name",
    "citizenship_no": "नागरिकता नं. · Citizenship no.",
    "dob_bs": "जन्म मिति (वि.सं.) · Date of birth (BS)",
    "dob_ad": "जन्म तिथि · Date of birth",
    "ward": "वडा नं. · Ward",
    "boid": "वीओआईडी · BOID",
    "crn": "सीआरएन · CRN",
    "bank": "बैंक · Bank",
    "phone": "मोबाइल · Mobile",
}


@dataclass
class DocSession:
    kind: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = "reading"  # reading | ready | confirmed | error
    error: str = ""
    fields: list[DocField] = field(default_factory=list)
    width: int = 0
    height: int = 0
    image: bytes = field(default=b"", repr=False)
    confirmed: dict[str, str] = field(default_factory=dict, repr=False)
    created: float = field(default_factory=time.monotonic)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def public(self) -> dict[str, Any]:
        """What the page may see. Candidate values are for the confirm screen only."""
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "status": self.status,
                "error": self.error,
                "width": self.width,
                "height": self.height,
                "fields": [
                    {
                        "key": f.key,
                        "label": FIELD_LABELS.get(f.key, f.key),
                        "value": f.value,
                        "status": f.status,
                        "box": f.box,
                    }
                    for f in self.fields
                ],
            }

    def confirm(self, values: dict[str, str]) -> dict[str, str]:
        """Accept the person's final values. Returns {key: problem} (empty = accepted). A blank
        value leaves that field out; at least one value is needed."""
        with self._lock:
            if self.status not in ("ready", "confirmed"):
                return {"": "the document is not ready to confirm"}
            allowed = {f.key for f in self.fields}
            problems: dict[str, str] = {}
            clean: dict[str, str] = {}
            for key, raw in values.items():
                if key not in allowed:
                    problems[key] = "not a field of this document"
                    continue
                if not raw.strip():
                    continue
                value = normalize_field(key, raw)
                if value is None:
                    problems[key] = "this does not look like a valid " + key.replace("_", " ")
                else:
                    clean[key] = value
            if not clean and not problems:
                problems[""] = "confirm at least one value"
            if problems:
                return problems
            self.confirmed = clean
            self.status = "confirmed"
            return {}

    def wipe(self) -> None:
        with self._lock:
            self.image = b""
            self.fields = []
            self.confirmed = {}


class DocumentStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._docs: dict[str, DocSession] = {}

    def _expire(self) -> None:
        now = time.monotonic()
        for doc_id in [d for d, s in self._docs.items() if now - s.created > TTL_S]:
            self._docs.pop(doc_id).wipe()

    def add(self, doc: DocSession) -> None:
        with self._lock:
            self._expire()
            while len(self._docs) >= MAX_DOCUMENTS:
                self._docs.pop(next(iter(self._docs))).wipe()
            self._docs[doc.id] = doc

    def get(self, doc_id: str) -> DocSession | None:
        with self._lock:
            self._expire()
            return self._docs.get(doc_id)

    def delete(self, doc_id: str) -> bool:
        with self._lock:
            doc = self._docs.pop(doc_id, None)
        if doc is None:
            return False
        doc.wipe()
        return True

    def take_confirmed(self, doc_id: str) -> dict[str, str] | None:
        """The confirmed values, once: the document is wiped as it is handed over. None if the
        document is unknown or the person has not confirmed it."""
        with self._lock:
            doc = self._docs.get(doc_id)
            if doc is None or doc.status != "confirmed":
                return None
            self._docs.pop(doc_id)
        values = dict(doc.confirmed)
        doc.wipe()
        return values


def _installed(installed: set[str], model: str) -> bool:
    return model in installed or f"{model}:latest" in installed


def read_document(doc: DocSession, photo: bytes, settings: Settings) -> None:
    """Fill `doc` from `photo` on a worker thread. Never raises; a failure is shown on the page."""
    llm = LLMClient(settings)
    try:
        second = settings.vision_model_b
        try:
            installed = set(llm.list_models())
        except LLMError:
            doc.error, doc.status = (
                "The local model server (Ollama) is not reachable. Start Ollama and try again.",
                "error",
            )
            return
        if (
            settings.vision_model not in installed
            and f"{settings.vision_model}:latest" not in installed
        ):
            doc.error, doc.status = (
                f"The vision model {settings.vision_model} is not installed. "
                f"Run: ollama pull {settings.vision_model}",
                "error",
            )
            return
        if second and not _installed(installed, second):
            second = None  # one reader only: nothing will be marked as agreed
        result = extract_document(
            llm,
            photo,
            DOCUMENT_KINDS[doc.kind]["keys"],
            model_a=settings.vision_model,
            model_b=second,
        )
        with doc._lock:
            doc.fields, doc.image = result.fields, result.image
            doc.width, doc.height = result.width, result.height
            doc.status = "ready"
    except DocumentError as exc:
        doc.error, doc.status = str(exc), "error"
    except Exception as exc:  # a broken read is shown on the page, never crashes the server
        doc.error, doc.status = f"{type(exc).__name__} while reading the photo", "error"
    finally:
        llm.close()
