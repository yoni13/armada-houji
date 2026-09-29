#!/usr/bin/env python3
"""Bounded QMI-to-NMEA/JSON Unix socket test; does not enable GPS at boot.

The parent socket directory must already exist, be owned by the caller, and
not be writable by other users. Its group receives socket access. Position
coordinates travel only over private pipes/sockets, never diagnostic logs.
JSON preserves optional QMI uncertainty fields; NMEA RMC cannot carry them.
"""
import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import selectors
import signal
import socket
import stat
import subprocess


def coordinate(value, width):
    units = round(abs(value) * 60_000_000)
    degrees, minutes = divmod(units, 60_000_000)
    whole, fraction = divmod(minutes, 1_000_000)
    return f"{degrees:0{width}d}{whole:02d}.{fraction:06d}"


def position_record(record):
    """Validate one report and retain raw QMI uncertainty without conversion.

    When both forms of uncertainty are present, QMI's confidence applies to
    the ellipse. Missing fields remain null, never a guessed accuracy value.
    """
    if not isinstance(record, dict):
        raise ValueError("Invalid position record")
    if record.get("fix") is not True:
        return {"fix": False}
    lat, lon, milliseconds = record.get("lat"), record.get("lon"), record.get("utc_ms")
    if (type(lat) not in (int, float) or type(lon) not in (int, float)
            or not math.isfinite(lat) or not math.isfinite(lon) or abs(lat) > 90 or abs(lon) > 180
            or type(milliseconds) is not int or not 0 <= milliseconds <= 4_102_444_799_999):
        raise ValueError("Invalid satellite fix")
    result = {"fix": True, "utc_ms": milliseconds, "lat": lat, "lon": lon}
    for name in ("horizontal_uncertainty_m", "horizontal_semi_major_m", "horizontal_semi_minor_m"):
        value = record.get(name)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise ValueError("Invalid horizontal uncertainty")
        result[name] = value
    confidence = record.get("horizontal_confidence_percent")
    if confidence is not None and (type(confidence) is not int or not 0 <= confidence <= 99):
        raise ValueError("Invalid horizontal confidence")
    result["horizontal_confidence_percent"] = confidence
    return result


def json_frame(record):
    return (json.dumps(position_record(record), allow_nan=False, separators=(",", ":")) + "\n").encode("ascii")


def rmc(record):
    record = position_record(record)
    valid = record.get("fix") is True
    fields = ["GNRMC", "", "V", "", "", "", "", "", "", "", "", "", "N"]
    if valid:
        lat, lon = float(record["lat"]), float(record["lon"])
        milliseconds = record["utc_ms"]
        when = dt.datetime.fromtimestamp(milliseconds // 1000, dt.timezone.utc)
        fields[1] = when.strftime("%H%M%S") + f".{milliseconds % 1000:03d}"
        fields[2:7] = ["A", coordinate(lat, 2), "S" if lat < 0 else "N",
                       coordinate(lon, 3), "W" if lon < 0 else "E"]
        fields[9], fields[12] = when.strftime("%d%m%y"), "A"
    body = ",".join(fields)
    checksum = 0
    for byte in body.encode("ascii"):
        checksum ^= byte
    return f"${body}*{checksum:02X}\r\n".encode("ascii")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--node", type=int, default=0)
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--format", choices=("nmea", "json"), default="nmea",
                        help="Use JSON to preserve raw QMI uncertainty; RMC is diagnostic only")
    args = parser.parse_args()
    if not 10 <= args.seconds <= 900 or not 0 <= args.node <= 0xffffffff:
        parser.error("Invalid node or duration")
    parent = args.socket.parent.stat()
    if not args.socket.is_absolute() or parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
        parser.error("Socket parent must be private and owned by the caller")
    # Never replace an existing socket or file.
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    os.umask(0o077)
    server.bind(str(args.socket))
    created = args.socket.stat()
    child = None
    read_fd = write_fd = None
    clients = set()
    selector = selectors.DefaultSelector()
    stopped = False
    def stop(*unused):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        os.chown(args.socket, -1, parent.st_gid)
        os.chmod(args.socket, 0o660)
        server.listen(4)
        server.setblocking(False)
        read_fd, write_fd = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        selector.register(server, selectors.EVENT_READ, "accept")
        selector.register(read_fd, selectors.EVENT_READ, "position")
        child = subprocess.Popen([str(args.probe), str(args.node), str(args.seconds), "json-fd", str(write_fd)],
                                 pass_fds=(write_fd,))
        os.close(write_fd)
        write_fd = None
        buffer = b""
        messages = fixes = 0
        while not stopped and child.poll() is None:
            for key, _ in selector.select(timeout=1):
                if key.data == "accept":
                    client, _ = server.accept()
                    client.setblocking(False)
                    if len(clients) >= 4:
                        client.close()
                    else:
                        clients.add(client)
                    continue
                data = os.read(read_fd, 4096)
                if not data:
                    stopped = True
                    break
                buffer += data
                if len(buffer) > 8192:
                    raise ValueError("Oversized position frame")
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    record = json.loads(line)
                    sentence = json_frame(record) if args.format == "json" else rmc(record)
                    messages += 1
                    fixes += record.get("fix") is True
                    for client in tuple(clients):
                        try:
                            # Drop a slow/disconnected client, never stall the receiver.
                            if client.send(sentence) != len(sentence):
                                raise ConnectionError("Partial socket write")
                        except OSError:
                            clients.remove(client)
                            client.close()
        print(f"Location bridge summary format={args.format} messages={messages} satellite_fixes={fixes}", flush=True)
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=25)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        selector.close()
        for client in clients:
            client.close()
        server.close()
        for fd in (read_fd, write_fd):
            if fd is not None:
                os.close(fd)
        if args.socket.exists() and args.socket.stat().st_ino == created.st_ino and stat.S_ISSOCK(args.socket.stat().st_mode):
            args.socket.unlink()
    return child.returncode if child is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
