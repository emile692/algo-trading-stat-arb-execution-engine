class RiskManager:
    def __init__(self, config):
        self.max_position = config["max_position"]

    def validate(self, signal, current_position):
        if signal in ("LONG", "SHORT") and abs(current_position) >= self.max_position:
            return False
        return True
