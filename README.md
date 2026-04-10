# SDN Link Failure Detection and Recovery

> **Course Project** | SDN Mininet Simulation | OpenFlow 1.0 + POX Controller

---

## Problem Statement

In traditional networks, link failures require manual intervention or slow convergence of distributed routing protocols. In an **SDN architecture**, the centralized controller has a global view of the topology and can instantly detect link failures and reprogram flow rules to restore connectivity — often within seconds.

This project implements:
- **Topology monitoring** — controller tracks all connected switches and ports
- **Link failure detection** — via `PortStatus` events (LINK DOWN)
- **Dynamic flow rule update** — stale flows deleted immediately on failure
- **Connectivity recovery** — MAC re-learning routes traffic automatically

---

## Network Topology

```
        h1   h2   h3   h4
         \    |    |   /
          \   |    |  /
              s1
```

- 1 switch (s1)
- 4 hosts (h1, h2, h3, h4)
- All hosts connected to the single switch

---

## File Structure

```
sdn_link_failure/
├── link_failure.py   # POX controller (main logic)
└── README.md         # This file
```

---

## Prerequisites

```bash
# Mininet
sudo apt install mininet openvswitch-switch

# POX (no pip needed, just clone)
git clone https://github.com/noxrepo/pox.git
```

> Tested on Debian Trixie with Python 3.13

---

## Setup & Execution

**Step 1 — Copy controller into POX:**
```bash
cp link_failure.py ~/pox/ext/
```

**Step 2 — Terminal 1: Start POX controller:**
```bash
cd ~/pox
python3 pox.py link_failure openflow.discovery
```

**Step 3 — Terminal 2: Start Mininet:**
```bash
sudo mn --topo single,4 --controller remote,ip=127.0.0.1,port=6633 --switch ovsk,protocols=OpenFlow10
```

### Controller Running
![Controller Running](controller.png)
<img width="1600" height="641" alt="image" src="https://github.com/user-attachments/assets/3be9f48f-6960-4a8b-9ac2-b81493895555" />


---

## Test Scenarios

### Scenario 1 – Normal Connectivity

```
mininet> pingall
```

<img width="998" height="306" alt="image" src="https://github.com/user-attachments/assets/5d89b03a-df9f-4d7b-92d1-d2123a3278fc" />

```
Results: 0% dropped (12/12 received)
```

![Normal Connectivity](normal.png)

---

### Scenario 2 – Link Failure Detection

```
mininet> link s1 h1 down
mininet> pingall
```

<img width="898" height="332" alt="image" src="https://github.com/user-attachments/assets/8c52cc71-96e2-4258-8155-b3f81c5f9e8b" />

```
h1 -> X X X        ← h1 isolated (link cut)
h2 -> X h3 h4      ← rest of network still works
Results: 50% dropped (6/12 received)
```

![Link Failure](failure.png)

---

### Scenario 3 – Recovery

```
mininet> link s1 h1 up
mininet> pingall
```

<img width="1008" height="338" alt="image" src="https://github.com/user-attachments/assets/fd735b14-a378-47eb-bbe3-9a9457813515" />


```
Results: 0% dropped (12/12 received)
```

![Recovery](recovery.png)

---

## Controller Event Flow

```
Switch connects
    └─→ Install table-miss rule (priority 0, send to controller)

Packet arrives (unknown destination)
    └─→ PacketIn event
         ├─→ Learn src_mac → in_port
         └─→ Flood or install flow rule

Link goes down
    └─→ PortStatus event (LINK DOWN)
         ├─→ Log failure timestamp
         ├─→ Delete stale flows on affected port
         ├─→ Clear MAC table
         └─→ Re-learning happens automatically
```

---

## Key OpenFlow Concepts Used

| Concept | Where Used |
|---------|-----------|
| Table-miss rule (priority 0) | Initial setup on every switch |
| `PacketIn` event | MAC learning, flow installation |
| Match fields: `in_port`, `eth_dst` | Flow rule matching |
| `OFPP_FLOOD` action | Unknown destination handling |
| `OFPFC_DELETE` command | Removing stale flows on failed port |
| `idle_timeout`, `hard_timeout` | Flow expiry (30s idle, 120s hard) |
| Port status events | Link failure detection |

---

## Useful Mininet CLI Commands

```bash
mininet> pingall                     # test all-pairs connectivity
mininet> h1 ping -c 5 h2             # ping between two hosts
mininet> link s1 h1 down             # simulate link failure
mininet> link s1 h1 up               # restore link
mininet> sh ovs-ofctl dump-flows s1  # view flow table
```

---

## References

1. POX SDN Controller – https://github.com/noxrepo/pox
2. OpenFlow 1.0 Specification – https://opennetworking.org
3. Mininet Documentation – http://mininet.org/
4. Open vSwitch Documentation – https://docs.openvswitch.org/
