# YUGITO Auth Server - compatibility wrapper for device/start response hotfix
# The full production server is preserved verbatim in yugito_auth_server_core.py.
# This wrapper changes only POST /api/device/start so HTTP 200 is sent before
# closing the PostgreSQL connection, with flush=True diagnostics around each step.

from __future__ import annotations

import secrets
import urllib.parse

import yugito_auth_server_core as core
from yugito_auth_server_core import *  # compatibility for existing imports

_ORIGINAL_DO_POST = core.Handler.do_POST


def _device_start_hotfix(self):
    if self.path != "/api/device/start":
        return _ORIGINAL_DO_POST(self)

    conn = None
    try:
        print("[device-start-hotfix] ENTER", flush=True)
        body = self._body()
        code = secrets.token_urlsafe(32)
        print("[device-start-hotfix] BODY_OK -> DB_OPEN", flush=True)
        conn = core.db()
        print("[device-start-hotfix] DB_OPEN_OK -> INSERT", flush=True)
        conn.execute(
            "INSERT INTO devices(device_code,platform,created_at,expires_at) VALUES(?,?,?,?)",
            (
                code,
                str(body.get("platform") or "unknown")[:20],
                core.now(),
                core.now() + core.DEVICE_TTL,
            ),
        )
        print("[device-start-hotfix] INSERT_OK -> COMMIT", flush=True)
        conn.commit()
        print("[device-start-hotfix] COMMIT_OK -> HTTP_200", flush=True)

        # Important diagnostic change: answer Android BEFORE conn.close().
        # The original code closed PostgreSQL first. Supabase proves the INSERT was
        # committed while Android never received the response.
        self._json(
            200,
            {
                "ok": True,
                "device_code": code,
                "verification_url": core.PUBLIC_BASE + "/login?device_code=" + urllib.parse.quote(code),
                "google_client_id": core.GOOGLE_CLIENT_ID,
                "native_google": True,
                "expires_in": core.DEVICE_TTL,
                "interval": 2,
            },
        )
        try:
            self.wfile.flush()
        except Exception:
            pass
        print("[device-start-hotfix] HTTP_200_SENT -> DB_CLOSE", flush=True)
        return
    except Exception as exc:
        print("[device-start-hotfix] ERROR " + repr(exc), flush=True)
        try:
            self._json(500, {"ok": False, "error": str(exc)})
        except Exception as response_exc:
            print("[device-start-hotfix] ERROR_RESPONSE_FAILED " + repr(response_exc), flush=True)
        return
    finally:
        if conn is not None:
            try:
                conn.close()
                print("[device-start-hotfix] DB_CLOSE_OK", flush=True)
            except Exception as close_exc:
                print("[device-start-hotfix] DB_CLOSE_ERROR " + repr(close_exc), flush=True)


core.Handler.do_POST = _device_start_hotfix
Handler = core.Handler


def main():
    core.main()


if __name__ == "__main__":
    main()
