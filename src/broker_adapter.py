from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BrokerAdapter(ABC):
    """
    내부 paper broker와 향후 외부 broker adapter가 맞춰야 할 최소 인터페이스.
    """

    @abstractmethod
    def get_account_state(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def refresh_market_prices(self, price_map: dict[str, float]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def submit_market_order(
        self,
        *,
        event_id: str,
        ticker: str,
        side: str,
        qty: float,
        fill_price: float,
        order_type: str = "paper_market",
        notes: str = "",
        detail_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError
