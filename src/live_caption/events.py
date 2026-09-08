from dataclasses import dataclass
from typing import Any


@dataclass
class RawASREvent:
    """A response received from the ASR service."""

    t: float
    kind: str
    text: str
    payload: Any = None

    def to_record(self) -> dict:
        return {
            "type": "asr",
            "t": self.t,
            "kind": self.kind,
            "text": self.text,
            "payload": self.payload,
        }

    @classmethod
    def from_record(cls, record: dict) -> "RawASREvent":
        return cls(
            t=float(record["t"]),
            kind=record["kind"],
            text=record.get("text", ""),
            payload=record.get("payload"),
        )
