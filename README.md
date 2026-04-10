# SDN Link Failure Detection and Recovery

> **Course Project** | SDN Mininet Simulation | OpenFlow 1.3 + Ryu Controller

---

## Problem Statement

In traditional networks, link failures require manual intervention or slow convergence of distributed routing protocols (OSPF, STP). In an **SDN architecture**, the centralized controller has a global view of the topology and can instantly detect link failures and reprogram flow rules to restore connectivity — often within seconds.

This project implements:
- **Topology discovery** using LLDP (via Ryu's `--observe-links`)
- **Link failure detection** via `EventLinkDelete` and `EventOFPPortStatus`
- **Dynamic flow rule update** — stale flows are deleted immediately on failure
- **Connectivity recovery** — MAC re-learning routes traffic over alternate paths
- **Performance measurement** using `ping` (latency) and `iperf` (throughput)

---

## Network Topology

```
         h1(10.0.0.1)        h2(10.0.0.2)
              |                    |
             s1 ────────────────  s2
              |  \              /  |
              |   \            /   |
             s3 ────────────── s4
              |                    |
         h3(10.0.0.3)        h4(10.0.0.4)
```

**Links:**
| Link | Purpose |
|------|---------|
| h1-s1, h2-s2, h3-s3, h4-s4 | Host access links (100 Mbps, 2ms) |
| s1-s2, s2-s4, s4-s3, s3-s1 | Ring backbone |
| s1-s4 | Diagonal alternate path |

The diagonal link `s1-s4` ensures that even if `s1-s2` fails, `h1` can still reach `h2` via `s1 → s4 → s2`.

---

## File Structure

```
sdn_link_failure/
├── controller.py     # Ryu OpenFlow 1.3 controller (main logic)
├── topology.py       # Mininet topology + automated test scenarios
├── run.sh            # Convenience runner script
└── README.md         # This file
```

---

## Prerequisites

```bash
# Python packages
pip install ryu networkx

# System packages
sudo apt install mininet openvswitch-switch wireshark iperf
```

> Tested on Ubuntu 20.04 / 22.04

---

## Setup & Execution

### Option A – Using the runner script (recommended)

```bash
# Make executable
chmod +x run.sh

# Interactive mode (Mininet CLI)
./run.sh

# Run both test scenarios automatically
./run.sh --test both

# Run only link failure scenario
./run.sh --test scenario2

# Run iperf test
./run.sh --iperf

# Clean up Mininet state
./run.sh --clean
```

### Option B – Manual (two terminals)

**Terminal 1 – Start Ryu controller:**
```bash
ryu-manager --observe-links controller.py
```

**Terminal 2 – Start Mininet:**
```bash
sudo python3 topology.py
```

---

## Test Scenarios

### Scenario 1 – Normal Connectivity

Verifies that all 4 hosts can reach each other under normal conditions.

```bash
./run.sh --test scenario1
```

**Expected output:**
```
  ✓ PASS  h1 (10.0.0.1) --> h2 (10.0.0.2)  | 0% packet loss
  ✓ PASS  h1 (10.0.0.1) --> h3 (10.0.0.3)  | 0% packet loss
  ✓ PASS  h1 (10.0.0.1) --> h4 (10.0.0.4)  | 0% packet loss
  ...
  Result: 6/6 paths reachable
```

---

### Scenario 2 – Link Failure & Recovery

Simulates failure of link `s1 ↔ s2` and verifies that traffic between `h1` and `h2` recovers via the alternate path `s1 → s4 → s2`.

```bash
./run.sh --test scenario2
```

**Expected output:**
```
[Step A] Baseline – ping h1 -> h2 before any failure
  Before failure: 0% packet loss

[Step B] Bringing DOWN link s1 <-> s2
  Interface s1-eth2 (s1<->s2) brought DOWN

[Step C] Post-failure – ping h1 -> h2 (expecting recovery)
  After  failure: 0% packet loss    ← recovered via s1->s4->s2

[Step D] Verify h1 -> h3 (should be unaffected)
  h1 -> h3: 0% packet loss

[Step E] Restoring link s1 <-> s2
  After restore: 0% packet loss

  Recovery Result: ✓ PASS – Connectivity restored via alternate path
```

---

## Controller Event Flow

```
Switch connects
    └─→ Install table-miss rule (priority 0, send to controller)

Topology discovery (LLDP)
    └─→ EventLinkAdd → graph.add_edge()

Packet arrives (unknown destination)
    └─→ EventOFPPacketIn
         ├─→ Learn src_mac → in_port
         └─→ Flood or install flow rule

Link goes down
    └─→ EventLinkDelete
         ├─→ Log failure timestamp
         ├─→ graph.remove_edge()
         ├─→ Delete stale flows on affected ports (OFPFC_DELETE)
         ├─→ Clear MAC table
         └─→ Re-learning happens automatically → traffic uses alternate path
```

---

## Performance Metrics

| Metric | Tool | Command |
|--------|------|---------|
| Latency | ping | `h1 ping -c 10 h2` |
| Throughput | iperf | `iperf -s` / `iperf -c 10.0.0.2 -t 10` |
| Flow tables | ovs-ofctl | `ovs-ofctl -O OpenFlow13 dump-flows s1` |
| Packet stats | ovs-ofctl | `ovs-ofctl -O OpenFlow13 dump-ports s1` |

### Useful Mininet CLI commands

```bash
# Inside Mininet CLI
mininet> pingall                    # test all-pairs connectivity
mininet> h1 ping -c 5 h2            # ping between hosts
mininet> h2 iperf -s &              # start iperf server
mininet> h1 iperf -c 10.0.0.2 -t 5 # iperf throughput test
mininet> s1 ovs-ofctl -O OpenFlow13 dump-flows s1  # flow table
mininet> link s1 s2 down            # bring down a link
mininet> link s1 s2 up              # restore a link
```

### Wireshark capture

```bash
# Capture on s1's interface to observe LLDP and OpenFlow messages
sudo wireshark -i s1-eth1 &
```

---

## Key OpenFlow Concepts Used

| Concept | Where Used |
|---------|-----------|
| Table-miss rule (priority 0) | Initial setup on every switch |
| `packet_in` event | MAC learning, flow installation |
| Match fields: `in_port`, `eth_dst` | Flow rule matching |
| `OFPP_FLOOD` action | Unknown destination handling |
| `OFPFC_DELETE` command | Removing stale flows on failed port |
| `idle_timeout`, `hard_timeout` | Flow expiry (30s idle, 120s hard) |
| Port status events | Secondary failure detection |
| LLDP topology discovery | `--observe-links` flag |

---

## References

1. Ryu SDN Framework Documentation – https://ryu.readthedocs.io/
2. OpenFlow 1.3 Specification – https://opennetworking.org/wp-content/uploads/2014/10/openflow-spec-v1.3.0.pdf
3. Mininet Documentation – http://mininet.org/
4. NetworkX Graph Library – https://networkx.org/
5. Open vSwitch Documentation – https://docs.openvswitch.org/
6. Feamster, N., Rexford, J., Zegura, E. (2014). *The Road to SDN: An Intellectual History of Programmable Networks*. ACM SIGCOMM Computer Communication Review.
