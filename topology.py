# topology.py
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info
import time
import os

def create_hospital_topology():
    info('*** Initializing Mininet for Hospital IoMT Network\n')
    net = Mininet(
        switch=OVSSwitch,
        controller=RemoteController,
        link=TCLink
    )

    # 1. Controller
    info('*** Adding Ryu Controller\n')
    c0 = net.addController('c0', ip='127.0.0.1', port=6633)

    # 2. Switches (3 departments) - CRITICAL: OpenFlow13 must be set
    info('*** Adding Department Switches (OpenFlow 1.3)\n')
    s1 = net.addSwitch('s1', protocols='OpenFlow13')  # ICU
    s2 = net.addSwitch('s2', protocols='OpenFlow13')  # Pharmacy
    s3 = net.addSwitch('s3', protocols='OpenFlow13')  # Admin

    # 3. ICU Devices
    info('*** Adding ICU Devices\n')
    h1 = net.addHost('h1', ip='10.0.0.1', mac='00:00:00:00:00:01')  # Ventilator
    h2 = net.addHost('h2', ip='10.0.0.2', mac='00:00:00:00:00:02')  # ICU Monitor
    h3 = net.addHost('h3', ip='10.0.0.3', mac='00:00:00:00:00:03')  # Infusion Pump

    # 4. Pharmacy Devices
    info('*** Adding Pharmacy Devices\n')
    h4 = net.addHost('h4', ip='10.0.0.4', mac='00:00:00:00:00:04')  # Pharmacy PC
    h5 = net.addHost('h5', ip='10.0.0.5', mac='00:00:00:00:00:05')  # Drug Dispenser
    h6 = net.addHost('h6', ip='10.0.0.6', mac='00:00:00:00:00:06')  # Pharmacy DB

    # 5. Admin Devices
    info('*** Adding Admin Devices\n')
    h7 = net.addHost('h7', ip='10.0.0.7', mac='00:00:00:00:00:07')  # Admin PC
    h8 = net.addHost('h8', ip='10.0.0.8', mac='00:00:00:00:00:08')  # Ward Monitor
    h9 = net.addHost('h9', ip='10.0.0.9', mac='00:00:00:00:00:09')  # Nurses Station

    # 6. Attacker — h10
    info('*** Adding Attacker Node\n')
    h10 = net.addHost('h10', ip='10.0.0.10', mac='00:00:00:00:00:10')

    # 7. Links — hosts to switches
    info('*** Creating Links\n')
    for h in [h1, h2, h3, h10]: # Attacker connected to ICU switch
        net.addLink(h, s1)
    for h in [h4, h5, h6]: 
        net.addLink(h, s2)
    for h in [h7, h8, h9]: 
        net.addLink(h, s3)

    # 8. Inter-switch links
    info('*** Connecting Departments\n')
    net.addLink(s1, s2)
    net.addLink(s2, s3)

    return net


# ---------------------------------------------------------------------------
# Hosts that must have a UDP listener on port 5005 running BEFORE simulators.
# h1/h2/h3 are senders only — they are never targeted, so no listener needed.
# ---------------------------------------------------------------------------
LISTENER_HOSTS = ['h4', 'h5', 'h6', 'h7', 'h8', 'h9']
LISTENER_PORT  = 5005
LISTENER_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'simulators', 'udp_listener.py'
)


def start_listeners(net):
    """Launch a silent UDP discard listener on each receiver host (background).

    The listener prevents ICMP port-unreachable replies when a simulator sends
    a packet before the target host has anything bound on port 5005.
    """
    info('*** Starting UDP listeners (ports 5005 & 5006) on receiver hosts\n')
    for hname in LISTENER_HOSTS:
        h = net.get(hname)
        # Pass host identity via env var so listener can log its own name
        h.cmd(f'MININET_HOST={hname} python3 {LISTENER_SCRIPT} > '
              f'/tmp/listener_{hname}.log 2>&1 &')

    # Give listeners a moment to bind before we confirm
    time.sleep(0.5)
    _confirm_listeners(net)


def _confirm_listeners(net):
    """Check each receiver host has something bound on ports 5005 AND 5006.

    Uses 'ss -ulnp' inside each host namespace (mininet runs each host in its
    own network namespace, so ss reflects that host's sockets only).
    If a listener failed to bind on either port, an explicit WARNING is printed.
    """
    info('*** Confirming UDP listeners are bound (ports 5005 & 5006)\n')
    all_ok = True
    for hname in LISTENER_HOSTS:
        h = net.get(hname)
        host_ok = True
        for port in [5005, 5006]:
            out = h.cmd(f'ss -ulnp sport = :{port}')
            if str(port) in out:
                info(f'    [{hname}] UDP {port} ✓ bound\n')
            else:
                info(f'    [{hname}] WARNING — UDP {port} NOT bound! '
                     f'Check /tmp/listener_{hname}.log\n')
                host_ok = False
        if not host_ok:
            all_ok = False
    if all_ok:
        info('*** All 6 listeners confirmed on ports 5005 & 5006 — safe to start simulators\n')
    else:
        info('*** WARNING: One or more listeners missing. '
             'Start simulators only after fixing the above.\n')

if __name__ == '__main__':
    # Set logging to see Mininet output
    setLogLevel('info')
    
    # Create the network
    hospital_net = create_hospital_topology()
    
    # Start the network
    info('*** Starting Network\n')
    hospital_net.start()

    # Start UDP listeners on receiver hosts BEFORE simulators are launched.
    # h4, h5, h6, h7, h8, h9 each get a silent discard listener on port 5005.
    # h1, h2, h3 are senders only — no listener needed.
    start_listeners(hospital_net)

    # Open the Mininet Command Line Interface     
    info('*** Running CLI\n')
    CLI(hospital_net)
    
    # Stop the network when the user exits the CLI
    info('*** Stopping Network\n')
    hospital_net.stop()