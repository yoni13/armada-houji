#!/usr/bin/python3
"""Houji Settings' Decky backend: the helper socket and the activation-code listener."""
import asyncio
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('backend', Path(__file__).with_name('houji-settings') / 'main.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class HelperSocket(unittest.TestCase):
    def test_request_reaches_helper_with_eof_and_reply_returns(self):
        with tempfile.TemporaryDirectory() as name:
            path = str(Path(name) / 'houji-settings.sock')
            received = []
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(path)
            server.listen(1)

            def serve():
                connection, _ = server.accept()
                with connection:
                    data = b''
                    # Like the helper's stdin read, this only finishes at EOF.
                    while chunk := connection.recv(4096):
                        data += chunk
                    received.append(json.loads(data))
                    connection.sendall(b'{"ok": true, "result": {"done": 1}}\n')

            thread = threading.Thread(target=serve)
            thread.start()
            with mock.patch.object(backend, 'SOCKET', path):
                reply = backend.helper({'op': 'status'})
            thread.join(5)
            server.close()
        self.assertEqual(received, [{'op': 'status'}])
        self.assertEqual(reply, {'ok': True, 'result': {'done': 1}})

    def test_missing_socket_is_reported_without_details(self):
        with mock.patch.object(backend, 'SOCKET', '/nonexistent/houji-settings.sock'):
            self.assertEqual(backend.helper({'op': 'status'}), backend.UNREACHABLE)

    def test_scan_notifications_ignore_duplicates_and_old_detections(self):
        detector = backend.ScanNotifications()
        state = {'enabled': True, 'reader': True, 'session': 'a', 'detections': 4}
        self.assertFalse(detector.update(state))
        self.assertFalse(detector.update(state))
        state['detections'] = 5
        self.assertTrue(detector.update(state))
        self.assertFalse(detector.update(state))
        # Notification off, switching modes, reloading plugin, and daemon restart
        # must not replay a tag already present when monitoring begins.
        self.assertFalse(detector.update({'enabled': False}))
        self.assertFalse(detector.update(state))
        self.assertFalse(detector.update(dict(state, reader=False)))
        self.assertFalse(detector.update(state))
        state.update(session='b', detections=0)
        self.assertFalse(detector.update(state))
        self.assertTrue(detector.update(dict(state, detections=1)))


class CodeListener(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        patcher = mock.patch.object(backend, 'helper', lambda request: self.calls.append(request) or {'ok': True})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.plugin = backend.Plugin()
        await self.plugin._main()
        self.addAsyncCleanup(self.plugin._unload)

    async def request(self, method, body=b'', path='/esim'):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.plugin.port)
        writer.write(f'{method} {path} HTTP/1.1\r\nHost: x\r\nContent-Length: {len(body)}\r\n'
                     'Content-Type: text/plain;charset=UTF-8\r\n\r\n'.encode() + body)
        await writer.drain()
        response = await reader.read()
        writer.close()
        head, _, payload = response.partition(b'\r\n\r\n')
        return head.decode(), payload

    async def test_token_is_single_use_and_code_reaches_helper(self):
        grant = await self.plugin.begin_esim_download()
        self.assertTrue(grant['url'].startswith('http://127.0.0.1:'))
        body = json.dumps({'token': grant['token'], 'code': 'LPA:1$test.invalid$X'}).encode()
        head, payload = await self.request('POST', body)
        self.assertIn('200 OK', head)
        self.assertIn('Access-Control-Allow-Origin: *', head)
        self.assertEqual(json.loads(payload), {'ok': True})
        self.assertEqual(self.calls, [{'op': 'esim.download', 'code': 'LPA:1$test.invalid$X'}])
        head, payload = await self.request('POST', body)
        self.assertIn('403', head)
        self.assertEqual(len(self.calls), 1)

    async def test_unknown_or_expired_tokens_and_preflight(self):
        head, _ = await self.request('POST', json.dumps({'token': 'guess', 'code': 'x'}).encode())
        self.assertIn('403', head)
        grant = await self.plugin.begin_esim_download()
        self.plugin.tokens[grant['token']] = (0, '/esim')
        head, _ = await self.request('POST', json.dumps({'token': grant['token'], 'code': 'x'}).encode())
        self.assertIn('403', head)
        head, payload = await self.request('OPTIONS')
        self.assertIn('204', head)
        self.assertEqual(payload, b'')
        head, _ = await self.request('POST', b'not json')
        self.assertIn('400', head)
        self.assertEqual(self.calls, [])

    async def test_nfc_tag_data_uses_private_channel_in_both_directions(self):
        tag = {'text': 'private tag contents', 'serial': '12:34:56:78', 'custom_serial': True}
        grant = await self.plugin.begin_nfc_request()
        self.assertNotIn(tag['text'], json.dumps(grant))
        with mock.patch.object(backend, 'helper', return_value={'ok': True, 'result': tag}):
            head, payload = await self.request('POST', json.dumps({
                'token': grant['token'], 'action': 'get'}).encode(), '/nfc')
        self.assertIn('200', head)
        self.assertEqual(json.loads(payload)['result'], tag)
        for action in ('save', 'start'):
            grant = await self.plugin.begin_nfc_request()
            _, payload = await self.request('POST', json.dumps({
                'token': grant['token'], 'action': action, **tag}).encode(), '/nfc')
            self.assertEqual(json.loads(payload), {'ok': True})
            self.assertEqual(self.calls[-1], {'op': 'nfc.tag.update', 'action': action, **tag})

    async def test_grants_cannot_cross_endpoints_or_run_arbitrary_helpers(self):
        grant = await self.plugin.begin_esim_download()
        head, _ = await self.request('POST', json.dumps({
            'token': grant['token'], 'action': 'get'}).encode(), '/nfc')
        self.assertIn('403', head)
        grant = await self.plugin.begin_nfc_request()
        head, _ = await self.request('POST', json.dumps({
            'token': grant['token'], 'action': 'shell', 'op': 'sim.select'}).encode(), '/nfc')
        self.assertIn('400', head)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
