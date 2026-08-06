from typing import Any, Dict

from strategies import get_strategy


class StrategyAdapter:
    def analyse(self, df: Any) -> Dict[str, Any]:
        return get_strategy("multi_factor")(df)


strategy = StrategyAdapter()

