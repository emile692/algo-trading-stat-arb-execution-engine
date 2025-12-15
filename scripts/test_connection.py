# scripts/test_connection.py

import sys
import logging
from infra.ibkr_connection import IBKRConnection
from ib_insync import Stock

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

def main():
    logging.info("Starting IBKR connection test...")

    # ⚠️ Paramètres : adapter si tu utilises IB Gateway
    HOST = "127.0.0.1"
    PORT = 4001
    CLIENT_ID = 1

    ibkr = IBKRConnection(host=HOST, port=PORT, client_id=CLIENT_ID)

    # ==== CONNECT ====
    try:
        ibkr.connect()
    except Exception as e:
        logging.error(f"Connection failed: {e}")
        sys.exit(1)

    if not ibkr.is_connected():
        logging.error("IBKR is not connected.")
        sys.exit(1)

    logging.info("IBKR connection OK.")

    # ==== MARKET DATA TEST ====
    logging.info("Testing market data on AAPL...")

    ib = ibkr.ib  # Access the underlying IB() instance

    contract = Stock(
        symbol="AIR",
        exchange="SMART",
        currency="EUR",
        primaryExchange="SBF"  # important pour éviter ambiguïtés
    )

    # Souscription aux données
    ib.reqMarketDataType(3)
    market_data = ib.reqMktData(contract, snapshot=False, regulatorySnapshot=False)

    # Attend quelques ticks
    logging.info("Waiting for price ticks (5 seconds)...")
    for _ in range(10):
        ibkr.heartbeat()  # Check connection
        ib.sleep(0.5)  # ib.sleep permet de garder l'event loop active

        if market_data.last or market_data.close:
            price = market_data.last or market_data.close
            logging.info(f"Received price tick: {price}")
            break

    if not (market_data.last or market_data.close):
        logging.warning("No market data received for AAPL.")

    # ==== DISCONNECT ====
    ibkr.disconnect()
    logging.info("Test completed successfully.")

if __name__ == "__main__":
    main()
