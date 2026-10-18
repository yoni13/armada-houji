#!/usr/bin/env python3
"""Native relay framing with a fake QTEE ABI; no modem or secure device access."""
import importlib.util
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import unittest

HERE=Path(__file__).resolve().parent;PORT=HERE.parent
sys.path.insert(0,str(PORT/'cellular'))
import qmi
spec=importlib.util.spec_from_file_location('license_reference',PORT/'cellular/license-relay.py')
reference=importlib.util.module_from_spec(spec);spec.loader.exec_module(reference)


class Relay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.root=Path(cls.tmp.name)
        subprocess.run(['make','-C',str(HERE),'OUT='+str(cls.root),str(cls.root/'houji-license-relay')],check=True)
        fake=cls.root/'qtee.c'
        fake.write_text(r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int houji_qtee_init(void){return 0;}
int houji_qtee_ready(void){return 0;}
int houji_qtee_check(unsigned secure,void *in,size_t n,void *out,size_t *size){
    const char *p=getenv("FAKE_CALLS");FILE*f=fopen(p,"a");fprintf(f,"%u %zu\n",secure,n);fclose(f);
    if(n && ((unsigned char*)in)[0]==0xfe){*size=0;return -5;}
    if(n && ((unsigned char*)in)[0]==0xfd){*size=2048;return 0;}
    static int retry=0;
    if(n && ((unsigned char*)in)[0]==0xfc && retry++<2){*size=0;return -99;}
    memcpy(out,in,n);*size=n;return 0;
}
''')
        subprocess.run(['cc','-Wall','-Werror','-shared','-fPIC',str(fake),'-o',str(cls.root/'qtee.so')],check=True)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.work=Path(self.directory.name);self.calls=self.work/'calls'
        self.client,server=socket.socketpair(socket.AF_UNIX,socket.SOCK_DGRAM)
        self.addCleanup(self.client.close);self.client.settimeout(3)
        notify=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM);notify.bind(str(self.work/'notify'));notify.settimeout(3)
        self.addCleanup(notify.close)
        self.output=(self.work/'log').open('w');self.addCleanup(self.output.close)
        self.process=subprocess.Popen([str(self.root/'houji-license-relay'),'--qtee-library',str(self.root/'qtee.so'),'--test-fd',str(server.fileno())],
            pass_fds=(server.fileno(),),stdout=self.output,stderr=self.output,
            env=dict(os.environ,FAKE_CALLS=str(self.calls),NOTIFY_SOCKET=str(self.work/'notify')))
        server.close()
        def stop():
            if self.process.poll() is None:self.process.terminate();self.process.wait(timeout=5)
        self.addCleanup(stop)
        self.assertEqual(notify.recv(32),b'READY=1')
    def request(self,secure=1,payload=b'input'):
        fields={1:struct.pack('<I',1),2:b'\x12\x34',3:bytes([secure]),4:struct.pack('<H',len(payload))+payload}
        return qmi.packet(0,41,0x20,b''.join(qmi.tlv(k,v) for k,v in fields.items()))
    def test_success_ack_and_indication_are_byte_exact(self):
        for secure,size in ((0,0),(1,17),(0,1024)):
            payload=b'X'*size;self.client.send(self.request(secure,payload))
            self.assertEqual(self.client.recv(4096),qmi.packet(2,41,0x20,qmi.tlv(2,b'\0'*4)+qmi.tlv(0x10,b'\0'*4)))
            self.assertEqual(self.client.recv(4096),qmi.packet(4,0,0x20,reference.indication(b'\x12\x34',secure,0,payload)))
    def test_malformed_frames_never_reach_tee(self):
        good=self.request();bad=[b'',good[:-1],good+b'x',self.request(2),self.request(1,b'x'*1025),qmi.packet(0,41,0x20,qmi.tlv(1,b'abcd')*2)]
        for data in bad:self.client.send(data)
        self.client.settimeout(.2)
        with self.assertRaises(socket.timeout):self.client.recv(4096)
        self.assertFalse(self.calls.exists())
    def test_unknown_message_and_non_request(self):
        self.client.send(qmi.packet(0,2,0x99,b''))
        self.assertEqual(self.client.recv(4096),qmi.packet(2,2,0x99,qmi.tlv(2,struct.pack('<HH',1,0x47))))
        self.client.send(qmi.packet(4,2,0x20,b''));self.client.settimeout(.2)
        with self.assertRaises(socket.timeout):self.client.recv(4096)
        self.assertFalse(self.calls.exists())
    def test_tee_failure_never_echoes_payload_and_busy_is_retried(self):
        self.client.send(self.request(1,b'\xfeprivate'));self.client.recv(4096)
        self.assertEqual(self.client.recv(4096),qmi.packet(4,0,0x20,reference.indication(b'\x12\x34',1,6,b'')))
        self.client.send(self.request(1,b'\xfcretry'));self.client.recv(4096)
        self.assertEqual(self.client.recv(4096),qmi.packet(4,0,0x20,reference.indication(b'\x12\x34',1,0,b'\xfcretry')))
        self.assertEqual(len(self.calls.read_text().splitlines()),4)
        self.assertNotIn('private',(self.work/'log').read_text())
    def test_oversized_tee_response_fails_instead_of_reading_out_of_bounds(self):
        self.client.send(self.request(1,b'\xfd'));self.client.recv(4096)
        self.assertNotEqual(self.process.wait(timeout=5),0)


if __name__=='__main__':unittest.main()
