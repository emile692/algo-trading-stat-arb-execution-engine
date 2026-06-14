from __future__ import annotations

import asyncio
import logging
import socket
import time
from dataclasses import dataclass

from ib_insync import IB


COMMON_IBKR_API_PORTS = (4001, 4002, 7496, 7497)


@dataclass(frozen=True)
class PortProbe:
    port: int
    reachable: bool


class IBKRConnectionError(RuntimeError):
    pass


def _probe_tcp_port(host: str, port: int, *, timeout_sec: float = 0.75) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_sec):
            return True
    except OSError:
        return False


def _common_port_probes(host: str, requested_port: int) -> list[PortProbe]:
    ordered_ports = [int(requested_port)] + [port for port in COMMON_IBKR_API_PORTS if int(port) != int(requested_port)]
    return [PortProbe(port=port, reachable=_probe_tcp_port(host, port)) for port in ordered_ports]


def _format_connection_error(*, host: str, port: int, client_id: int, exc: Exception) -> str:
    requested_reachable = _probe_tcp_port(host, port)
    probes = _common_port_probes(host, port)
    probe_summary = ", ".join(f"{probe.port}={'open' if probe.reachable else 'closed'}" for probe in probes)

    if requested_reachable:
        primary_hint = (
            f"TCP {host}:{port} is reachable, but the IBKR API handshake timed out or was rejected. "
            "Verify 'Enable ActiveX and Socket Clients', Trusted IPs, and any authorization popup in TWS/IB Gateway."
        )
    else:
        primary_hint = (
            f"TCP {host}:{port} is not accepting connections. "
            "Make sure the configured API port in TWS/IB Gateway matches the script."
        )

    return (
        f"Failed to connect to IBKR on {host}:{port} with client_id={client_id}: "
        f"{type(exc).__name__}: {exc or '(no detail)'} | {primary_hint} | "
        f"Common port probe: {probe_summary}"
    )


class IBKRConnection:
    def __init__(self, host="127.0.0.1", port=4001, client_id=1):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.ib = IB()
        self._last_heartbeat = time.time()

    def connect(self, *, timeout_sec: float = 10.0):
        try:
            self.ib.connect(self.host, self.port, clientId=self.client_id, timeout=timeout_sec)
            logging.info("Connected to IBKR")
        except (ConnectionRefusedError, TimeoutError, asyncio.TimeoutError, OSError) as exc:
            message = _format_connection_error(
                host=self.host,
                port=self.port,
                client_id=self.client_id,
                exc=exc,
            )
            logging.error(message)
            raise IBKRConnectionError(message) from exc
        except Exception as exc:
            logging.error(f"Failed to connect to IBKR: {type(exc).__name__}: {exc}")
            raise

    def disconnect(self):
        if self.ib.isConnected():
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
                except Exception as exc:
                    logging.error(f"Reconnect failed: {exc}")

            self._last_heartbeat = time.time()
