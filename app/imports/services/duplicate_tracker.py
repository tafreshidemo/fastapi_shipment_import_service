from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class DuplicateTracker:
    seen_shipment_codes: set[str] = field(default_factory=set)

    def unseen_codes(self, shipment_codes: set[str]) -> set[str]:
        return shipment_codes - self.seen_shipment_codes

    def remember_codes(self, shipment_codes: set[str]) -> None:
        self.seen_shipment_codes.update(shipment_codes)
