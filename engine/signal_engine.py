class SignalEngine:
    def __init__(self, params):
        self.z_entry = params["z_entry"]
        self.z_exit = params["z_exit"]

    def compute_signal(self, spread, mean, std):
        z = (spread - mean) / std
        if z > self.z_entry:
            return "SHORT"
        elif z < -self.z_entry:
            return "LONG"
        elif abs(z) < self.z_exit:
            return "CLOSE"
        return None
