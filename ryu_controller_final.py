from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (
    MAIN_DISPATCHER, CONFIG_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
)
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, ipv4, tcp, udp
from ryu.lib import hub
import csv
import os
import time

NORMAL_INTERVAL = 1.0
FAST_INTERVAL = 0.1
FAST_MODE_HOLD = 2.5
FLOW_IDLE_TIMEOUT = 600
IP_FLOW_PRIORITY = 10
L2_FLOW_PRIORITY = 1

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)   # create logs/ dir if not present
HOST_BY_MAC = {f"00:00:00:00:00:{i:02d}": f"h{i}" for i in range(1, 11)}
HOST_BY_IP = {f"10.0.0.{i}": f"h{i}" for i in range(1, 11)}
ACCESS_DPID = {"h1": 1, "h2": 1, "h3": 1, "h10": 1, "h4": 2, "h5": 2, "h6": 2, "h7": 3, "h8": 3, "h9": 3}


class IoMTController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(IoMTController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.fast_mode = False
        self._fast_mode_until = 0.0
        self._pending = {}
        self._prev = {}
        self._last_reply_time = {}
        self.latest_summary = {}
        self.detect_module = None
        self._open_log()
        self.monitor_thread = hub.spawn(self._poll_flow_stats)
        print("\n[*] Ryu Controller Ready (FINAL verified version).")
        print(f"[*] Two-tier polling: normal={NORMAL_INTERVAL}s fast={FAST_INTERVAL}s")
        print(f"[*] Raw baseline log: {self._log_path}")
        print("[*] Waiting for switches to connect...")

    def _open_log(self):
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self._log_path = os.path.join(LOG_DIR, f"baseline_raw_{stamp}.csv")
        self._log_file = open(self._log_path, "w", newline="")
        self._log_writer = csv.writer(self._log_file)
        self._log_writer.writerow(["timestamp", "datapath_id", "host", "packet_delta", "distinct_dst_ips", "distinct_dst_ports", "fast_mode", "poll_interval_s"])
        self._log_file.flush()

    def _log_summary(self, now, dpid, summary, poll_interval):
        for host in sorted(summary):
            s = summary[host]
            self._log_writer.writerow([f"{now:.3f}", dpid, host, s["packets"], len(s["dst_ips"]), len(s["dst_ports"]), int(self.fast_mode), "" if poll_interval is None else f"{poll_interval:.3f}"])
        self._log_file.flush()

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        print(f"[+] Switch Connected: Datapath ID {datapath.id}")
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
        elif ev.state == DEAD_DISPATCHER:
            if self.datapaths.pop(dp.id, None) is not None:
                print(f"[-] Switch Disconnected: Datapath ID {dp.id}")
            self._pending.pop(dp.id, None)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None, idle_timeout=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        kwargs = dict(datapath=datapath, priority=priority, match=match, instructions=inst, idle_timeout=idle_timeout)
        if buffer_id:
            kwargs["buffer_id"] = buffer_id
        datapath.send_msg(parser.OFPFlowMod(**kwargs))

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        if eth.ethertype in (ether_types.ETH_TYPE_IPV6, ether_types.ETH_TYPE_LLDP):
            return
        dst = eth.dst
        src = eth.src
        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port
        out_port = self.mac_to_port[dpid][dst] if dst in self.mac_to_port[dpid] else ofproto.OFPP_FLOOD
        actions = [parser.OFPActionOutput(out_port)]

        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        if ip_pkt is not None:
            kw = dict(eth_type=ether_types.ETH_TYPE_IP, ipv4_src=ip_pkt.src, ipv4_dst=ip_pkt.dst, ip_proto=ip_pkt.proto)
            t = pkt.get_protocol(tcp.tcp)
            u = pkt.get_protocol(udp.udp)
            if t is not None:
                kw['tcp_dst'] = t.dst_port
            elif u is not None:
                kw['udp_dst'] = u.dst_port
            match = parser.OFPMatch(in_port=in_port, **kw)
            priority, idle = IP_FLOW_PRIORITY, FLOW_IDLE_TIMEOUT
        else:
            match = parser.OFPMatch(in_port=in_port, eth_type=ether_types.ETH_TYPE_ARP, eth_dst=dst, eth_src=src)
            priority, idle = L2_FLOW_PRIORITY, 0

        if out_port != ofproto.OFPP_FLOOD:
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self.add_flow(datapath, priority, match, actions, msg.buffer_id, idle)
                return
            self.add_flow(datapath, priority, match, actions, None, idle)

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)

    def trigger_fast_mode(self):
        self.fast_mode = True
        self._fast_mode_until = time.time() + FAST_MODE_HOLD

    def _poll_flow_stats(self):
        while True:
            if self.fast_mode and time.time() >= self._fast_mode_until:
                self.fast_mode = False
                print("[*] Back to NORMAL polling")
            for dp in list(self.datapaths.values()):
                try:
                    dp.send_msg(dp.ofproto_parser.OFPFlowStatsRequest(dp))
                except Exception as e:
                    print(f"[!] stats request failed on dp {dp.id}: {e}")
            hub.sleep(FAST_INTERVAL if self.fast_mode else NORMAL_INTERVAL)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        msg = ev.msg
        dpid = msg.datapath.id
        self._pending.setdefault(dpid, []).extend(msg.body)
        if msg.flags & msg.datapath.ofproto.OFPMPF_REPLY_MORE:
            return
        body = self._pending.pop(dpid, [])
        now = time.time()
        last = self._last_reply_time.get(dpid)
        poll_interval = None if last is None else now - last
        self._last_reply_time[dpid] = now
        try:
            summary = self._summarize(dpid, body)
            self.latest_summary[dpid] = summary
            self._log_summary(now, dpid, summary, poll_interval)
        except Exception as e:
            print(f"[!] stats processing failed on dp {dpid}: {e}")

    def _summarize(self, dpid, body):
        summary = {h: {"packets": 0, "dst_ips": set(), "dst_ports": set()} for h, d in ACCESS_DPID.items() if d == dpid}
        for stat in body:
            m = stat.match
            src_ip = m.get('ipv4_src')
            if src_ip is None:
                continue
            host = HOST_BY_IP.get(src_ip)
            if host is None or ACCESS_DPID.get(host) != dpid:
                continue
            dst_ip = m.get('ipv4_dst')
            proto = m.get('ip_proto')
            dport = m.get('tcp_dst') or m.get('udp_dst')
            key = (dpid, src_ip, dst_ip, proto, dport)
            duration = stat.duration_sec + stat.duration_nsec / 1e9
            prev = self._prev.get(key)
            if prev is None or stat.packet_count < prev[0] or duration < prev[1]:
                delta = stat.packet_count
            else:
                delta = stat.packet_count - prev[0]
            self._prev[key] = (stat.packet_count, duration)
            if delta > 0:
                s = summary[host]
                s["packets"] += delta
                s["dst_ips"].add(dst_ip)
                if dport is not None:
                    s["dst_ports"].add(dport)
        return summary
