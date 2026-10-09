import asyncio
import json
import secrets
import socket
import time
from contextlib import suppress

# Decky runs under FEX, where /usr/bin/python3 resolves to the x86 rootfs Python
# without the system's D-Bus bindings. houji-settings.socket starts the native
# helper for each connection instead.
SOCKET = "/run/houji-settings.sock"
TOKEN_SECONDS = 120
UNREACHABLE = {"ok": False, "error": "Houji Settings could not reach its system helper."}


class ScanNotifications:
    """Baseline once per enable/service session; notify only about new scans."""
    def __init__(self):
        self.previous = None

    def update(self, state):
        if not state.get('enabled') or not state.get('reader') or not state.get('session'):
            self.previous = None
            return False
        current = (state['session'], state.get('detections', 0))
        previous, self.previous = self.previous, current
        return previous is not None and previous[0] == current[0] and current[1] > previous[1]


def helper(request):
    # Requests can hold activation codes or NFC tag contents: use the socket, never
    # into arguments, and neither it nor a failure's details are logged here.
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(90)
            connection.connect(SOCKET)
            connection.sendall(json.dumps(request).encode())
            connection.shutdown(socket.SHUT_WR)
            data = b""
            while chunk := connection.recv(65536):
                data += chunk
        reply = json.loads(data)
    except (OSError, ValueError):
        return UNREACHABLE
    return reply if isinstance(reply, dict) else UNREACHABLE


def respond(status, body):
    payload = b"" if body is None else json.dumps(body).encode()
    reason = {200: "OK", 204: "No Content", 400: "Bad Request", 403: "Forbidden"}[status]
    return (f"HTTP/1.1 {status} {reason}\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "Access-Control-Allow-Methods: POST\r\n"
            "Access-Control-Allow-Headers: Content-Type\r\n"
            "Access-Control-Allow-Private-Network: true\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Connection: close\r\n\r\n").encode() + payload


class Plugin:
    async def _main(self):
        self.tokens = {}
        # Decky logs RPC arguments and results. Private NFC settings and eSIM
        # codes travel through this listener in both directions instead.
        self.server = await asyncio.start_server(self._receive_code, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.scan_task = asyncio.create_task(self._watch_scans())

    async def _unload(self):
        self.scan_task.cancel()
        with suppress(asyncio.CancelledError):
            await self.scan_task
        self.server.close()
        await self.server.wait_closed()

    async def _helper(self, op, **fields):
        return await asyncio.to_thread(helper, {"op": op, **fields})

    async def status(self):
        return await self._helper("status")

    async def set_rotation(self, mode):
        return await self._helper("rotation.set", mode=mode)

    async def set_charge_limit(self, limit):
        return await self._helper("charge_limit.set", limit=limit)

    async def set_nfc(self, enabled):
        return await self._helper("nfc.set", enabled=enabled)

    async def nfc_action(self, action):
        return await self._helper("nfc.action", action=action)

    async def set_nfc_notifications(self, enabled):
        return await self._helper("nfc.notifications", enabled=enabled)

    async def _watch_scans(self):
        detector = ScanNotifications()
        while True:
            await asyncio.sleep(2)
            try:
                reply = await self._helper('nfc.watch')
                if reply.get('ok') and detector.update(reply.get('result', {})):
                    import decky
                    await decky.emit('nfc_tag_found')
            except Exception:
                # Do not log private controller errors. Next poll retries.
                pass

    async def enable_cellular(self):
        return await self._helper("cellular.enable")

    async def select_sim(self, mode):
        return await self._helper("sim.select", mode=mode)

    async def refresh_profiles(self):
        return await self._helper("esim.refresh")

    async def change_profile(self, action, handle, nickname=None):
        return await self._helper("esim.profile", action=action, handle=handle, nickname=nickname)

    async def set_data(self, enabled):
        return await self._helper("data.set", enabled=enabled)

    async def set_roaming(self, allowed):
        return await self._helper("roaming.set", allowed=allowed)

    async def begin_esim_download(self):
        return self._grant("/esim")

    async def begin_nfc_request(self):
        return self._grant("/nfc")

    def _grant(self, path):
        now = time.monotonic()
        self.tokens = {token: grant for token, grant in self.tokens.items() if grant[0] > now}
        token = secrets.token_urlsafe(24)
        self.tokens[token] = (now + TOKEN_SECONDS, path)
        return {"url": f"http://127.0.0.1:{self.port}{path}", "token": token}

    async def _receive_code(self, reader, writer):
        status, body = 400, {"ok": False, "error": "Invalid request."}
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            lines = head.decode("latin-1").split("\r\n")
            method, path = lines[0].split(" ")[:2]
            headers = {key.strip().lower(): value.strip()
                       for key, _, value in (line.partition(":") for line in lines[1:] if line)}
            if method == "OPTIONS":
                status, body = 204, None
            elif method == "POST" and path in ("/esim", "/nfc"):
                length = int(headers.get("content-length", "0"))
                if 0 < length <= 8192:
                    data = json.loads(await asyncio.wait_for(reader.readexactly(length), 10))
                    token = data.get("token") if isinstance(data, dict) else None
                    deadline, allowed_path = self.tokens.pop(token, (0, None)) if isinstance(token, str) else (0, None)
                    if deadline > time.monotonic() and allowed_path == path:
                        if path == "/esim":
                            status, body = 200, await self._helper("esim.download", code=data.get("code"))
                        elif data.get("action") == "get":
                            status, body = 200, await self._helper("nfc.tag.get")
                        elif data.get("action") in ("save", "start"):
                            status, body = 200, await self._helper("nfc.tag.update", action=data["action"],
                                text=data.get("text"), serial=data.get("serial"), custom_serial=data.get("custom_serial"))
                    else:
                        status, body = 403, {"ok": False, "error": "The request expired. Try again."}
        except Exception:
            pass
        try:
            writer.write(respond(status, body))
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()
