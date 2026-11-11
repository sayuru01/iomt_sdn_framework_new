

# simulators/traffic_sim.py
import socket
import json
import time
import argparse
import random
from datetime import datetime
import sys
import os

# Add project root to path so 'database' package is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from clinical_ranges import generate_vitals, get_interval, CLINICAL_RANGES
from database.db_manager import safe_write

SERVER_PORT = 5005
SECONDARY_PORT = 5006
SECONDARY_PORT_PROB = 0.10  # 10% chance per cycle to also send on port 5006
EXTRA_TARGET_PROB  = 0.10  # 10% chance per cycle to send to one extra off-map target

# Extra (lateral-movement) target IP per jitter device — NOT in their normal connectivity map
EXTRA_TARGET_IP = {
    'h4': '10.0.0.7',  # h7
    'h5': '10.0.0.4',  # h4
    'h6': '10.0.0.9',  # h9
    'h7': '10.0.0.5',  # h5
    'h9': '10.0.0.6',  # h6  (note: h9 already talks to h6; kept for lateral-variance intent)
}


def get_all_targets(host_id):
    """Return all target IPs for a host based on the confirmed topology map.

    Topology (confirmed):
        h1 (Ventilator)    -> h9
        h2 (ICU Monitor)   -> h9, h7
        h3 (Infusion Pump) -> h9
        h4 (Pharmacy PC)   -> h6, h5, h9
        h5 (Drug Dispenser)-> h6, h9
        h6 (Pharmacy DB)   -> h7, h4
        h7 (Admin PC)      -> h6, h9
        h8 (Ward Monitor)  -> h9     
        h9 (Nurses Station)-> h6, h7, h8, h4
    """
    ALL_TARGETS = {
        'h1': ['10.0.0.9'],
        'h2': ['10.0.0.9', '10.0.0.7'],
        'h3': ['10.0.0.9'],
        'h4': ['10.0.0.6', '10.0.0.5', '10.0.0.9'],
        'h5': ['10.0.0.6', '10.0.0.9'],
        'h6': ['10.0.0.7', '10.0.0.4'],
        'h7': ['10.0.0.6', '10.0.0.9'],
        'h8': ['10.0.0.9'],
        'h9': ['10.0.0.6', '10.0.0.7', '10.0.0.8', '10.0.0.4'],
    }
    return ALL_TARGETS[host_id]


# Packets sent to EACH target per cycle (1 for medical devices, 50 for pharmacy/admin)
PACKETS_PER_CYCLE = {
    'h1': 1,
    'h2': 1,
    'h3': 1,
    'h4': 50,
    'h5': 50,
    'h6': 50,
    'h7': 50,
    'h8': 1,
    'h9': 50,
}


def save_to_database(host_id, device_type, vitals):
    """Save one reading to patient_data table via the shared db_manager."""
    try:
        safe_write("""
            INSERT INTO patient_data (
                timestamp, host_id, device_type,
                breathing_rate, tidal_volume, spo2_vent, airway_pressure, peep,
                heart_rate, blood_pressure, temperature, spo2_icu,
                flow_rate, volume_infused,
                prescription_id, drug_name, dispensed_amount,
                record_type, record_count
            ) VALUES (
                datetime('now'), ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?
            )
        """, (
            host_id, device_type,
            # Ventilator (h1)
            vitals.get('breathing_rate'), vitals.get('tidal_volume'),
            vitals.get('spo2') if device_type == 'VENTILATOR' else None,
            vitals.get('airway_pressure'), vitals.get('peep'),
            # ICU Monitor (h2) / Ward Monitor (h8)
            vitals.get('heart_rate'), vitals.get('blood_pressure'),
            vitals.get('temperature'),
            vitals.get('spo2') if device_type in ('ICU_MONITOR', 'WARD_MONITOR') else None,
            # Infusion Pump (h3)
            vitals.get('flow_rate'), vitals.get('volume_infused'),
            # Pharmacy-type devices (h4-h6)
            vitals.get('prescription_id'), vitals.get('drug_name'),
            vitals.get('dispensed_amount'),
            # Admin-type devices (h7-h9)
            vitals.get('record_type'), vitals.get('record_count')
        ))
    except Exception as e:
        print(f"[{host_id}] DB Error: {e}")


def run_simulator(host_id):
    if host_id not in CLINICAL_RANGES:
        raise ValueError(f"Unknown host_id '{host_id}' — must be h1-h9")

    device_type = CLINICAL_RANGES[host_id]['device_type']
    interval = get_interval(host_id)
    targets = get_all_targets(host_id)

    # h4, h5, h6, h7, h9 use per-cycle jitter; others use fixed count
    USE_JITTER = host_id in ('h4', 'h5', 'h6', 'h7', 'h9')
    base_pkt_count = PACKETS_PER_CYCLE[host_id]

    print(f"[*] Starting Simulator for {host_id} ({device_type}) — every {interval}s -> "
          f"{'~45-55 (jitter)' if USE_JITTER else base_pkt_count} pkt(s) each to {targets}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        while True:
            vitals = generate_vitals(host_id)

            # Per-cycle packet count: jitter for h4/h5/h6/h7/h9, fixed for others
            # One value per cycle — same count applied to all targets this cycle
            if USE_JITTER:
                pkt_count = random.randint(45, 55)
            else:
                pkt_count = base_pkt_count

            payload = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
                "host_id": host_id,
                "device_type": device_type,
                "data": vitals
            }

            # UDP send is OPTIONAL — never let it crash the simulator
            try:
                message = json.dumps(payload).encode('utf-8')

                # Per-cycle secondary port decision (h4/h5/h6/h7/h9 only)
                # ONE coin-flip per cycle — same result applied to ALL targets
                use_secondary = USE_JITTER and (random.random() < SECONDARY_PORT_PROB)

                # Per-cycle lateral-movement decision — INDEPENDENT of use_secondary
                # ONE coin-flip per cycle — extra target gets SAME pkt_count + SAME port logic
                use_extra_target = USE_JITTER and (random.random() < EXTRA_TARGET_PROB)
                extra_ip = EXTRA_TARGET_IP.get(host_id) if use_extra_target else None

                for _ip in targets:
                    for _ in range(pkt_count):
                        sock.sendto(message, (_ip, SERVER_PORT))
                    if use_secondary:
                        for _ in range(pkt_count):
                            sock.sendto(message, (_ip, SECONDARY_PORT))

                # Send to extra (off-map) target if triggered this cycle
                if extra_ip:
                    for _ in range(pkt_count):
                        sock.sendto(message, (extra_ip, SERVER_PORT))
                    if use_secondary:
                        for _ in range(pkt_count):
                            sock.sendto(message, (extra_ip, SECONDARY_PORT))
            except Exception as e:
                print(f"[{host_id}] UDP send skipped: {e}")

            print(f"[{host_id}] Sent: {vitals}")

            save_to_database(host_id, device_type, vitals)

            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n[*] Stopping simulator for {host_id}")
    finally:
        sock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='IoMT Device Simulator')
    parser.add_argument('--host', type=str, required=True,
                         help='Host ID (h1-h9)')
    args = parser.parse_args()
    run_simulator(args.host)