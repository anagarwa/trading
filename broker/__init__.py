from config import ACTIVE_BROKER
from broker.kite_broker import KiteBroker
from broker.breeze_broker import BreezeBroker
from broker.base_broker import BaseBroker


def get_broker() -> BaseBroker:
    if ACTIVE_BROKER == "kite":
        return KiteBroker()
    elif ACTIVE_BROKER == "breeze":
        return BreezeBroker()
    else:
        raise ValueError(
            f"Unknown broker: '{ACTIVE_BROKER}'. Valid values are 'kite' or 'breeze'."
        )
