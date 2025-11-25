# engine/stat_arb_pair.py

import numpy as np

class StatArbPair() :

    def __init__(self, name, leg1, leg2, hedge_ratio, z_entry, z_exit) :
        self._name = name
        self._leg1 = leg1
        self._leg2 = leg2
        self._hedge_ratio = hedge_ratio
        self._z_entry = z_entry
        self._z_exit = z_exit
        self._last_price_leg1 = None
        self._last_price_leg2 = None
        self._last_spread = None
        self._spread_history = []

    def update_prices(self, price1 : float, price2 : float) :
        self._last_price_leg1 = price1
        self._last_price_leg2 = price2
        self._last_spread = price1 - self._hedge_ratio * price2
        self._spread_history.append(self._last_spread)

    def compute_z_score(self, window : int , spread : float) -> float  :
        if len(self._spread_history) >= window :
            mean = np.mean(self._spread_history[-window:])
            sigma = np.std(self._spread_history[-window:])
            if sigma == 0 :
                return None
            z_score = (spread-mean)/ sigma
            return z_score
        else :
            raise ValueError("Not enough data to compute z-score")


    def get_latest_spread(self) -> float :
        return self._last_spread

    def reset_history(self) :
        self._spread_history.clear()

    def should_long(self, zscore) :
        return zscore <= -self._z_entry

    def should_short(self, zscore) :
        return zscore >= self._z_entry

    def should_close(self, zscore) :
        return abs(zscore) <= self._z_exit

    @property
    def leg1(self):
        return self._leg1

    @property
    def leg2(self):
        return self._leg2

    @property
    def hedge_ratio(self):
        return self._hedge_ratio

    @property
    def z_entry(self):
        return self._z_entry

    @property
    def z_exit(self):
        return self._z_exit




