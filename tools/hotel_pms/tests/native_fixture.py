"""Offline PMS boundary for the installed-runtime approval integration test."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

sys.path.insert(0, os.environ["HOTEL_TEST_SERVER_DIR"])
import server


def forbidden(*args, **kwargs):
    raise AssertionError("Network is forbidden in this fixture")


socket.socket = forbidden
server.http_json = forbidden
server.load_config = lambda: {}


def pms(config, name, payload, profile="read"):
    root = Path(os.environ["HOTEL_PMS_WORKSPACE"])
    if name == "agent_prepare_reservation":
        return {"status": "ready", "confirmationCode": "AB12", "preparedToken": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
                "expiresAt": (server.now_utc()+dt.timedelta(minutes=10)).isoformat(), "idempotencyKey": payload["guestName"], "summary": {"guestName": payload["guestName"]}}
    if name == "agent_create_reservation":
        with (root/"provider-calls.jsonl").open("a") as stream:
            stream.write(json.dumps({"name": name, "idempotencyKey": payload["idempotencyKey"], "actor": payload["sourceMetadata"]})+"\n")
        if (root/"lose-provider-response").exists():
            raise TimeoutError("Synthetic provider response lost after acceptance")
        return {"reservationId": "synthetic-reservation"}
    raise AssertionError(name)


server.pms_tool = pms
server.main()
