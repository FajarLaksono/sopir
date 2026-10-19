import socket, re, csv, time

HOST, PORT = "127.0.0.1", 3110
ANGLES = "-90 -75 -60 -45 -30 -20 -15 -10 -5 0 5 10 15 20 30 45 60 75 90"  # 19 track rangefinders

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(5.0)

# Handshake
while True:
    sock.sendto(f"SCR(init {ANGLES})".encode(), (HOST, PORT))
    try:
        data, _ = sock.recvfrom(1000)
        if b"***identified***" in data:
            break
    except socket.timeout:
        print("waiting for TORCS...")

def parse(msg):
    out = {}
    for k, v in re.findall(r"\((\w+) ([^)]*)\)", msg):
        vals = [float(x) for x in v.split()]
        out[k] = vals if len(vals) > 1 else vals[0]
    return out

def controller(s):
    steer = (s["angle"] - s["trackPos"] * 0.5) / 0.785398
    steer = max(-1, min(1, steer))
    target = 60
    accel = 0.6 if s["speedX"] < target else 0.0
    brake = 0.0
    gear = int(max(1, min(6, 1 + s["speedX"] // 45)))
    return dict(accel=accel, brake=brake, gear=gear, steer=steer)

f = open(f"telemetry_{int(time.time())}.csv", "w", newline="")
writer = None

try:
    while True:
        data, _ = sock.recvfrom(1000)
        msg = data.decode()
        if "***shutdown***" in msg or "***restart***" in msg:
            break
        s = parse(msg)
        a = controller(s)

        # flatten and log
        row = {}
        for k, v in s.items():
            if isinstance(v, list):
                for i, x in enumerate(v): row[f"{k}_{i}"] = x
            else:
                row[k] = v
        row.update({f"act_{k}": v for k, v in a.items()})
        if writer is None:
            writer = csv.DictWriter(f, fieldnames=list(row))
            writer.writeheader()
        writer.writerow(row)

        cmd = (f"(accel {a['accel']})(brake {a['brake']})(gear {a['gear']})"
               f"(steer {a['steer']})(clutch 0)(focus 0)(meta 0)")
        sock.sendto(cmd.encode(), (HOST, PORT))
finally:
    f.close()