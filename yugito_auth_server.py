from __future__ import annotations

import json
import secrets
import urllib.parse

import yugito_auth_server_core as core
from yugito_auth_server_core import *

_original_do_post = core.Handler.do_POST


def _probe_device_start(self):
    if self.path != "/api/device/start":
        return _original_do_post(self)

    conn = None
    try:
        print("[DEVICE_START_PROBE] ENTER", flush=True)
        body = self._body()
        code = secrets.token_urlsafe(32)
        conn = core.db()
        print("[DEVICE_START_PROBE] DB_OPEN", flush=True)
        conn.execute(
            "INSERT INTO devices(device_code,platform,created_at,expires_at) VALUES(?,?,?,?)",
            (code, str(body.get("platform") or "unknown")[:20], core.now(), core.now() + core.DEVICE_TTL),
        )
        conn.commit()
        print("[DEVICE_START_PROBE] COMMIT_OK", flush=True)

        payload = {
            "ok": True,
            "device_code": code,
            "verification_url": core.PUBLIC_BASE + "/login?device_code=" + urllib.parse.quote(code),
            "google_client_id": core.GOOGLE_CLIENT_ID,
            "native_google": True,
            "expires_in": core.DEVICE_TTL,
            "interval": 2,
        }
        raw = core.json_bytes(payload)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        print("[DEVICE_START_PROBE] HEADERS_SENT len=" + str(len(raw)), flush=True)
        self.wfile.write(raw)
        self.wfile.flush()
        self.close_connection = True
        print("[DEVICE_START_PROBE] BODY_FLUSHED_CONNECTION_CLOSE", flush=True)
        return
    except Exception as exc:
        print("[DEVICE_START_PROBE] ERROR " + repr(exc), flush=True)
        try:
            self._json(500, {"ok": False, "error": str(exc)})
        except Exception as exc2:
            print("[DEVICE_START_PROBE] ERROR_RESPONSE " + repr(exc2), flush=True)
        return
    finally:
        if conn is not None:
            try:
                conn.close()
                print("[DEVICE_START_PROBE] DB_CLOSE_OK", flush=True)
            except Exception as exc:
                print("[DEVICE_START_PROBE] DB_CLOSE_ERROR " + repr(exc), flush=True)


core.Handler.do_POST = _probe_device_start
Handler = core.Handler


def main():
    core.main()


if __name__ == "__main__":
    main()
