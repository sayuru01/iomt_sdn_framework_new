# IoMT SDN Framework — AI Studio Project Handover & Status Report

## 1. Project Overview & Architecture
This project is an **SDN-based Security Framework for an Internet of Medical Things (IoMT) Hospital Network**. It uses **Mininet** to emulate the physical network, **Ryu (OpenFlow 1.3)** as the central SDN controller, and custom Python simulators to generate realistic clinical & administrative network traffic.

### Topology Layout (`topology.py`)
* **Controller**: Ryu RemoteController on `127.0.0.1:6633` (OpenFlow 1.3)
* **Switches**: 3 OpenFlow 1.3 switches connected in a line topology (`s1 <-> s2 <-> s3`):
  * **s1 (ICU)**:
    * `h1` (Ventilator, IP: `10.0.0.1`, MAC: `00:00:00:00:00:01`)
    * `h2` (ICU Monitor, IP: `10.0.0.2`, MAC: `00:00:00:00:00:02`)
    * `h3` (Infusion Pump, IP: `10.0.0.3`, MAC: `00:00:00:00:00:03`)
    * `h10` (Attacker Node, IP: `10.0.0.10`, MAC: `00:00:00:00:00:10`)
  * **s2 (Pharmacy)**:
    * `h4` (Pharmacy PC, IP: `10.0.0.4`, MAC: `00:00:00:00:00:04`)
    * `h5` (Drug Dispenser, IP: `10.0.0.5`, MAC: `00:00:00:00:00:05`)
    * `h6` (Pharmacy DB, IP: `10.0.0.6`, MAC: `00:00:00:00:00:06`)
  * **s3 (Administration)**:
    * `h7` (Admin PC, IP: `10.0.0.7`, MAC: `00:00:00:00:00:07`)
    * `h8` (Ward Monitor, IP: `10.0.0.8`, MAC: `00:00:00:00:00:08`)
    * `h9` (Nurses Station, IP: `10.0.0.9`, MAC: `00:00:00:00:00:09`)
* **Default Switch Mode**: Mininet `OVSSwitch` defaults to `failMode='secure'`, meaning switches drop all traffic unless the controller is connected and installs flow rules.

---

## 2. Traffic Specifications & Connectivity Map
The network follows a strict medical-administrative traffic matrix implemented in `simulators/traffic_sim.py`:

| Device | Role | Targets (Receivers) | Interval | Packet Count per Target per Cycle | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **h1** | Ventilator | h9 | 1s | 1 | Medical sensor (fixed) |
| **h2** | ICU Monitor | h9, h7 | 1s | 1 | Medical sensor (fixed) |
| **h3** | Infusion Pump | h9 | 5s | 1 | Medical actuator (fixed) |
| **h4** | Pharmacy PC | h6, h5, h9 | 300s | 45–55 (jitter ±5) | Bulk data + 10% prob. secondary port 5006 |
| **h5** | Drug Dispenser | h6, h9 | 300s | 45–55 (jitter ±5) | Bulk data + 10% prob. secondary port 5006 |
| **h6** | Pharmacy DB | h7, h4 | 300s | 45–55 (jitter ±5) | Bulk data + 10% prob. secondary port 5006 |
| **h7** | Admin PC | h6, h9 | 300s | 45–55 (jitter ±5) | Bulk data + 10% prob. secondary port 5006 |
| **h8** | Ward Monitor | h9 | 1s | 1 | Medical sensor (fixed) |
| **h9** | Nurses Station | h6, h7, h8, h4 | 300s | 45–55 (jitter ±5) | Bulk data + 10% prob. secondary port 5006 |

---

## 3. Key Changes Implemented in This Session

### 1. Unified Connectivity & Jitter in `simulators/traffic_sim.py`
* **Resolved Incomplete Target Mapping**: Replaced the fragmented `get_target_ip()` and `EXTRA_TARGETS` patch with a single clean `get_all_targets()` dictionary covering all 9 devices and multiple recipients.
* **Added Packet Jitter (Part 1)**: For bulk devices (`h4, h5, h6, h7, h9`), packet counts are randomized per cycle (`random.randint(45, 55)`), with the exact same count applied across all targets in that cycle to preserve consistency.
* **Added Secondary Port Variance (Part 2)**: Added a 10% probability check per cycle (`random.random() < 0.10`). When triggered, traffic is sent to both port `5005` and `5006` across all targets.
* **Fixed Syntax Bug**: Removed stray closing brackets at the end of the file that caused an `unmatched '}' SyntaxError`.

### 2. Multi-Port Discard UDP Listener (`simulators/udp_listener.py`)
* Created a lightweight background UDP discard server.
* Uses Python `threading` to simultaneously bind to both `0.0.0.0:5005` and `0.0.0.0:5006` in a single process.
* Silently reads and discards incoming UDP payloads to prevent Linux from sending ICMP Port-Unreachable replies back to sender hosts.

### 3. Startup Verification in `topology.py`
* Added `start_listeners(net)` and `_confirm_listeners(net)`:
  * Automatically starts `udp_listener.py` on the 6 receiver hosts (`h4, h5, h6, h7, h8, h9`).
  * Uses `ss -ulnp sport = :<port>` directly inside each host's network namespace to verify that ports 5005 and 5006 are actively bound before the Mininet CLI opens.
  * Correctly skips send-only hosts (`h1, h2, h3`).

### 4. Controller Log Directory Fix in `ryu_controller_final.py`
* **Fix Applied & Confirmed Working**:
  ```python
  LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
  os.makedirs(LOG_DIR, exist_ok=True)
  ```
  * Before this fix, Ryu crashed immediately upon invocation during `_open_log()` before any switches could connect.
  * After this fix, all three switches (`s1, s2, s3`) successfully connect (`Datapath ID 1, 2, 3`).

---

## 4. Primary Ongoing Problems & Root Cause Analysis

### Problem A: Ryu Controller Process Gets "Terminated"
* **Symptoms**:
  * Ryu starts with `ryu-manager ryu_controller_final.py`.
  * Switches connect: `[+] Switch Connected: Datapath ID 1`, `2`, `3`.
  * Shortly thereafter, the Ryu process halts with a `Terminated` message in the terminal.
* **Likely Causes to Investigate**:
  1. **Background Polling Thread Exception**:
     * In `ryu_controller_final.py`, `self.monitor_thread = hub.spawn(self._poll_flow_stats)`.
     * `_poll_flow_stats` calls `dp.send_msg(dp.ofproto_parser.OFPFlowStatsRequest(dp))`.
     * If an unhandled exception or greenlet crash occurs inside Eventlet / Ryu hub during polling or stat parsing (`_summarize`), it can terminate the Ryu process.
  2. **Signal Termination (SIGTERM)**:
     * When running `sudo mn -c` in the same shell or another terminal, Mininet's cleanup scripts may kill running controller processes (`pkill -9 ryu-manager`).
     * Closing terminal sessions or shell backgrounding without `nohup` / separate process groups.
  3. **Eventlet / Python Compatibility**:
     * Eventlet monkey-patching incompatibilities with modern Python threading / socket libraries.

### Problem B: Mininet `pingall` Complete Failure (`X` for All Hosts)
* **Symptoms**:
  * Running `pingall` inside the Mininet CLI shows `X` across all hosts (e.g. `h1 -> X X X X X X X X X`).
* **Technical Factors**:
  1. **Controller Dependency in Secure Mode**:
     * Because switches use OpenFlow 1.3 and `failMode='secure'`, when the Ryu controller terminates or drops offline, all packets fail table lookup and are discarded by OVS.
  2. **Inter-Switch Forwarding & ARP Flow Matching**:
     * In `ryu_controller_final.py` (`_packet_in_handler`):
       ```python
       match = parser.OFPMatch(in_port=in_port, eth_type=ether_types.ETH_TYPE_ARP, eth_dst=dst, eth_src=src)
       ```
     * When hosts send ARP broadcasts (`dst = ff:ff:ff:ff:ff:ff`), `out_port` is set to `OFPP_FLOOD`.
     * The code does:
       ```python
       if out_port != ofproto.OFPP_FLOOD:
           self.add_flow(...)
       ```
     * For broadcast ARP, `add_flow` is skipped (which is normal for flooding), but the packet is output via `OFPPacketOut`.
     * However, over a 3-switch chain (`s1 - s2 - s3`), if the controller is unstable or packet-out handling does not properly reach across the inter-switch links (`s1-s2`, `s2-s3`), ARP resolution fails, causing ICMP ping packets to never be emitted.

---

## 5. Next Steps for AI Studio
1. **Stabilize Ryu Process Lifecycle**:
   * Inspect the exact terminal stdout/stderr trace when Ryu reports `Terminated`.
   * Add robust `try...except` logging around `_poll_flow_stats`, `_request_flow_stats`, and `flow_stats_reply_handler`.
   * Ensure Ryu is run in a dedicated terminal with explicit output redirection (e.g., `ryu-manager ryu_controller_final.py 2>&1 | tee ryu_debug.log`).
2. **Verify Basic L2 Flow Installation**:
   * Test switch connectivity with a standalone, minimal L2 learning controller (`ryu.app.simple_switch_13`) to isolate whether `pingall` works independently of the baseline logging / security module code.
   * Verify flow entries in OVS directly using:
     ```bash
     sudo ovs-ofctl -O OpenFlow13 dump-flows s1
     sudo ovs-ofctl -O OpenFlow13 dump-flows s2
     sudo ovs-ofctl -O OpenFlow13 dump-flows s3
     ```
3. **Run Full IoMT Baseline Data Collection**:
   * Once `pingall` passes with 0% dropped packets, run the device traffic simulators (`python3 simulators/traffic_sim.py --host hX`) and verify data ingestion into `database/iomt_security.db` and the CSV flow baseline logs.
