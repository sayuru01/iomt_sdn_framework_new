# simulators/udp_listener.py
#
# Purpose: Bind UDP ports 5005 AND 5006 on a host so incoming packets are
#          silently discarded instead of triggering ICMP port-unreachable replies.
#
#   Port 5005 — primary traffic from all device simulators
#   Port 5006 — secondary port used ~10% of cycles by jitter hosts (PART 2)
#
# Run on: h4, h5, h6, h7, h8, h9  (the 6 hosts that RECEIVE traffic)
# Do NOT run on: h1, h2, h3        (they only send, never receive)
#
# Both ports are handled inside ONE process using two threads — one thread
# per socket — so a single background '&' launch covers both ports.
#
# Usage (inside Mininet via topology.py — not called directly):
#   hX.cmd('MININET_HOST=hX python3 /path/to/udp_listener.py > /tmp/listener_hX.log 2>&1 &')

import socket
import sys
import os
import threading

LISTEN_PORTS = [5005, 5006]
BUFSIZE      = 65535


def _drain(port, host_id):
    """Bind one UDP socket and silently discard every packet received."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(('0.0.0.0', port))
    except OSError as e:
        print(f"[udp_listener][{host_id}] ERROR: Cannot bind port {port}: {e}",
              flush=True)
        sys.exit(1)

    print(f"[udp_listener][{host_id}] Listening on UDP port {port} — packets will be discarded",
          flush=True)

    while True:
        try:
            sock.recvfrom(BUFSIZE)
        except Exception:
            pass   # never crash; just keep draining


def main():
    host_id = os.environ.get('MININET_HOST', 'unknown')

    threads = []
    for port in LISTEN_PORTS:
        t = threading.Thread(target=_drain, args=(port, host_id), daemon=True)
        t.start()
        threads.append(t)

    # Keep the main thread alive so the daemon threads stay running
    for t in threads:
        t.join()


if __name__ == '__main__':
    main()
