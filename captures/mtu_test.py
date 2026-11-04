import socket, struct, time, select, re, sys
H = bytes.fromhex
ADDR = sys.argv[1] if len(sys.argv) > 1 else "XX:XX:XX:XX:XX:XX"
IMTU = int(sys.argv[2]) if len(sys.argv) > 2 else 2582
SOL_L2CAP, L2CAP_OPTIONS = 6, 1
WXP = H("04000400170000001000")

s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
s.setsockopt(274, 4, struct.pack("BB", 2, 0))
opts = bytearray(s.getsockopt(SOL_L2CAP, L2CAP_OPTIONS, 12))
print("L2CAP options avant:", struct.unpack_from("<HH", opts))
struct.pack_into("<H", opts, 2, IMTU)
s.setsockopt(SOL_L2CAP, L2CAP_OPTIONS, bytes(opts))
s.settimeout(8)
s.connect((ADDR, 0x1001))
print("L2CAP options après connect:", struct.unpack_from("<HH", s.getsockopt(SOL_L2CAP, L2CAP_OPTIONS, 12)))

seq = [0x41]
def wx(body):
    seq[0] += 1
    b = bytes([0x08, seq[0]]) + body
    return WXP + len(b).to_bytes(2, "little") + b
def req(svc, iv):
    return wx(bytes([0x42, 0x0b, 0x08, svc, 0x10, 0x02, 0x1a, 0x05, 0x01]) + iv.to_bytes(4, "little"))

def listen(t, label):
    end = time.time() + t; data = 0; seen = []
    while time.time() < end:
        r, _, _ = select.select([s], [], [], 0.05)
        if not r: continue
        p = s.recv(8192)
        if p[:4] != H("04000400") or len(p) < 6 or p[4] != 0x17: continue
        if b"<?xml" in p:
            m = re.search(rb"AccessoryService</key><string>(\w+)", p)
            seen.append(f"ANNONCE 0x800 {m.group(1).decode() if m else '?'} len={len(p)}")
        elif p[:10] == WXP:
            body = p[12:]
            if b"\x3a" in body[:6] and len(p) > 60:
                data += 1
                if data <= 2: seen.append(f"DATA len={len(p)} {p[:40].hex()}")
            else:
                names = re.findall(rb"[a-z0-9]{4,}", body)
                seen.append(f"WX len={len(p)} {body[:40].hex()} {names[:3]}")
        else:
            seen.append(f"0x17 autre {p[:24].hex()} len={len(p)}")
    print(f"[{label}] data={data}", *seen[:12], sep="\n   ", flush=True)
    return data

for p in ("00000400010002000000000000000000", "040004004d00d700000000000000", "040004000f00ffffffff"):
    s.send(H(p)); time.sleep(0.15)
listen(3, "init")
s.send(wx(b"\x22\x00")); s.send(wx(b"\x10\x02\x22\x00"))
listen(2, "request_all_descriptors x2")
s.send(req(14, 40000)); listen(1, "start 14")
s.send(req(16, 40000)); n = listen(3, "start 16")
s.send(req(14, 0))
if not n:
    for svc in (15, 79, 7, 9, 6, 11, 12):
        s.send(req(svc, 40000))
        if listen(1.5, f"start {svc}"): break
s.close()
