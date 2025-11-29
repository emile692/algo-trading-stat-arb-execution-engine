from ib_insync import *
import time
import logging

class IBKRConnection:
    def __init__(self, host="127.0.0.1", port=4002, client_id=1):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.ib = IB()
        self._last_heartbeat = time.time()

    def connect(self):
        try:
            self.ib.connect(self.host, self.port, clientId=self.client_id)
            logging.info("Connected to IBKR")
        except Exception as e:
            logging.error(f"Failed to connect to IBKR: {e}")
            raise e

    def disconnect(self):
        self.ib.disconnect()
        logging.info("Disconnected from IBKR")

    def is_connected(self):
        return self.ib.isConnected()

    def heartbeat(self):
        """
        Check that we are connected every 5 seconds.
        Reconnect automatically if needed.
        """
        if time.time() - self._last_heartbeat > 5:
            if not self.is_connected():
                logging.warning("IBKR disconnected, reconnecting...")
                try:
                    self.connect()
                except Exception as e:
                    logging.error(f"Reconnect failed: {e}")

            self._last_heartbeat = time.time()
