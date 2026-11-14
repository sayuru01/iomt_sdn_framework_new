"""Save clean host flows and restore a verified snapshot on demand.

Run the snapshot only after the topology is stable and before traffic simulators
start. Restoring a host deletes *all* flows on its access switch.
"""

import hashlib
import json
from pathlib import Path
import re
import subprocess


BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups"
HOST_IPS = {f"h{i}": f"10.0.0.{i}" for i in range(1, 10)}
ACCESS_DPID = {
    "h1": 1, "h2": 1, "h3": 1,
    "h4": 2, "h5": 2, "h6": 2,
    "h7": 3, "h8": 3, "h9": 3,
}
# These dump-only counters and timing fields cannot be replayed by add-flow.
_DUMP_ONLY = {"duration", "n_packets", "n_bytes", "idle_age", "hard_age"}


def _hash_data(data):
    """Use the same deterministic JSON representation for saving and checking."""
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _run_ofctl(*args):
    return subprocess.run(
        ["ovs-ofctl", "-O", "OpenFlow13", *args],
        check=True, capture_output=True, text=True,
    ).stdout


def _host_rules(dump, ip):
    """Extract replayable rules with this exact IPv4 source or destination."""
    ip_field = re.compile(r"(?:^|,)(?:nw_src|nw_dst|ipv4_src|ipv4_dst)=" + re.escape(ip) + r"(?=,|\s|$)")
    rules = []
    for line in dump.splitlines():
        if " actions=" not in line:
            continue
        match_part, actions = line.strip().split(" actions=", 1)
        if not ip_field.search(match_part):
            continue
        fields = [field.strip() for field in match_part.split(",")]
        fields = [field for field in fields if field.split("=", 1)[0] not in _DUMP_ONLY]
        rules.append(",".join(fields) + ",actions=" + actions)
    return rules


def take_snapshot():
    """Capture h1–h9 after pingall succeeds, before simulators start."""
    # Read every switch before writing any backup, so a failed dump aborts early.
    dumps = {dpid: _run_ofctl("dump-flows", f"s{dpid}") for dpid in sorted(set(ACCESS_DPID.values()))}
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for host, ip in HOST_IPS.items():
        dpid = ACCESS_DPID[host]
        data = {"host": host, "ip": ip, "dpid": dpid, "flow_rules": _host_rules(dumps[dpid], ip)}
        digest = _hash_data(data)
        manifest[host] = digest
        (BACKUP_DIR / f"{host}_clean.json").write_text(
            json.dumps({**data, "hash": digest}, indent=2) + "\n", encoding="utf-8"
        )
        print(f"[+] {host}: saved {len(data['flow_rules'])} flow rule(s)")
    (BACKUP_DIR / "manifest_hashes.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"[+] Snapshots saved in {BACKUP_DIR}")


def verify_and_restore(host_id):
    """Verify a host snapshot, replace its access switch flows, and ping it.

    Returns True only when the restoration commands and the ping succeed.
    Deleting flows affects every host on the switch, not just host_id.
    """
    if host_id not in HOST_IPS:
        raise ValueError(f"Unknown or excluded host: {host_id}")
    try:
        backup = json.loads((BACKUP_DIR / f"{host_id}_clean.json").read_text(encoding="utf-8"))
        stored_hash = backup.pop("hash")
        valid = (
            stored_hash == _hash_data(backup)
            and backup.get("host") == host_id
            and backup.get("ip") == HOST_IPS[host_id]
            and backup.get("dpid") == ACCESS_DPID[host_id]
            and isinstance(backup.get("flow_rules"), list)
            and all(isinstance(rule, str) for rule in backup["flow_rules"])
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        valid = False
    if not valid:
        print("[!] Backup integrity check FAILED for " + host_id)
        return False

    switch = f"s{backup['dpid']}"
    try:
        _run_ofctl("del-flows", switch)
        for rule in backup["flow_rules"]:
            _run_ofctl("add-flow", switch, rule)
        ping = subprocess.run(["ping", "-c", "1", backup["ip"]], check=False, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"[!] Restore failed for {host_id}: {exc}")
        return False
    if ping.returncode != 0:
        print(f"[!] Flows restored for {host_id}, but ping failed")
        return False
    print(f"[+] Flows restored and ping succeeded for {host_id} ({backup['ip']})")
    return True


if __name__ == "__main__":
    take_snapshot()
