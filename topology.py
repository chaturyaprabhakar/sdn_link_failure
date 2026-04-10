import argparse
import time
import sys

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info, warn, error
from mininet.cli import CLI


CONTROLLER_IP = "127.0.0.1"
CONTROLLER_PORT = 6633


def build_topology():
    """Create and return the Mininet network (not started)."""
    net = Mininet(
        controller=RemoteController,
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=False,
    )

    info("*** Adding Ryu remote controller\n")
    c0 = net.addController(
        "c0",
        controller=RemoteController,
        ip=CONTROLLER_IP,
        port=CONTROLLER_PORT,
    )

    info("*** Adding switches\n")
    s1 = net.addSwitch("s1", protocols="OpenFlow10")
    s2 = net.addSwitch("s2", protocols="OpenFlow10")
    s3 = net.addSwitch("s3", protocols="OpenFlow10")
    s4 = net.addSwitch("s4", protocols="OpenFlow10")

    info("*** Adding hosts\n")
    h1 = net.addHost("h1", ip="10.0.0.1/24")
    h2 = net.addHost("h2", ip="10.0.0.2/24")
    h3 = net.addHost("h3", ip="10.0.0.3/24")
    h4 = net.addHost("h4", ip="10.0.0.4/24")

    link_opts = dict(bw=100, delay="2ms", loss=0)

    info("*** Adding host-switch links\n")
    net.addLink(h1, s1, **link_opts)
    net.addLink(h2, s2, **link_opts)
    net.addLink(h3, s3, **link_opts)
    net.addLink(h4, s4, **link_opts)

    info("*** Adding switch-switch links (ring + diagonal)\n")
    net.addLink(s1, s2, **link_opts)   # ring
    net.addLink(s2, s4, **link_opts)   # ring
    net.addLink(s4, s3, **link_opts)   # ring
    net.addLink(s3, s1, **link_opts)   # ring
    net.addLink(s1, s4, **link_opts)   # diagonal alternate path

    return net


def print_banner(msg):
    print("\n" + "=" * 65)
    print("  " + msg)
    print("=" * 65)


def scenario1_normal_connectivity(net):
    """Test 1: All hosts can reach each other under normal conditions."""
    print_banner("SCENARIO 1 – Normal Connectivity (All hosts reachable)")
    time.sleep(3)  # let controller install flows

    hosts = [net.get(h) for h in ["h1", "h2", "h3", "h4"]]
    pairs = [("h1", "h2"), ("h1", "h3"), ("h1", "h4"),
             ("h2", "h3"), ("h2", "h4"), ("h3", "h4")]

    results = {}
    for src_name, dst_name in pairs:
        src = net.get(src_name)
        dst = net.get(dst_name)
        result = src.cmd(f"ping -c 3 -W 2 {dst.IP()}")
        loss_line = [l for l in result.splitlines() if "packet loss" in l]
        loss = loss_line[0] if loss_line else "unknown"
        ok = "0% packet loss" in loss
        results[(src_name, dst_name)] = ok
        status = "✓ PASS" if ok else "✗ FAIL"
        print(f"  {status}  {src_name} ({src.IP()}) --> {dst_name} ({dst.IP()})  | {loss.strip()}")

    passed = sum(results.values())
    total = len(results)
    print(f"\n  Result: {passed}/{total} paths reachable")
    return passed == total


def scenario2_link_failure_recovery(net):
    """
    Test 2: Fail link s1-s2, verify connectivity is recovered via alternate path.
    Path before failure:  h1 -> s1 -> s2 -> h2
    Path after  failure:  h1 -> s1 -> s4 -> s2 -> h2  (via diagonal + ring)
    """
    print_banner("SCENARIO 2 – Link Failure Detection & Recovery")

    s1 = net.get("s1")
    s2 = net.get("s2")
    h1 = net.get("h1")
    h2 = net.get("h2")
    h3 = net.get("h3")

    # Step A: Confirm connectivity before failure
    info("\n[Step A] Baseline – ping h1 -> h2 before any failure\n")
    result_before = h1.cmd(f"ping -c 5 -W 2 {h2.IP()}")
    loss_before = [l for l in result_before.splitlines() if "packet loss" in l]
    print("  Before failure:", loss_before[0].strip() if loss_before else "no output")

    # Step B: Bring down link s1-s2
    info("\n[Step B] Bringing DOWN link s1 <-> s2 (simulating failure)\n")
    # Find the interface on s1 that connects to s2
    intf_s1_s2 = None
    for intf in s1.intfList():
        link = intf.link
        if link and (link.intf2.node == s2 or link.intf1.node == s2):
            intf_s1_s2 = intf
            break

    if intf_s1_s2:
        s1.cmd(f"ifconfig {intf_s1_s2.name} down")
        # Also bring down the s2 side
        other = intf_s1_s2.link.intf2 if intf_s1_s2.link.intf1 == intf_s1_s2 else intf_s1_s2.link.intf1
        other.node.cmd(f"ifconfig {other.name} down")
        print(f"  Interface {intf_s1_s2.name} (s1<->s2) brought DOWN")
    else:
        warn("  Could not find s1-s2 interface automatically\n")

    print("  Waiting 5 seconds for controller to detect failure and update flows...")
    time.sleep(5)

    # Step C: Ping after failure – should still work via alternate path
    info("\n[Step C] Post-failure – ping h1 -> h2 (expecting recovery)\n")
    result_after = h1.cmd(f"ping -c 5 -W 3 {h2.IP()}")
    loss_after = [l for l in result_after.splitlines() if "packet loss" in l]
    print("  After  failure:", loss_after[0].strip() if loss_after else "no output")

    # Step D: Also test h1 -> h3 (different path, should be unaffected)
    info("\n[Step D] Verify h1 -> h3 (should be unaffected by s1-s2 failure)\n")
    result_h3 = h1.cmd(f"ping -c 5 -W 2 {h3.IP()}")
    loss_h3 = [l for l in result_h3.splitlines() if "packet loss" in l]
    print("  h1 -> h3:", loss_h3[0].strip() if loss_h3 else "no output")

    # Step E: Restore the link
    info("\n[Step E] Restoring link s1 <-> s2\n")
    if intf_s1_s2:
        s1.cmd(f"ifconfig {intf_s1_s2.name} up")
        other.node.cmd(f"ifconfig {other.name} up")
        print(f"  Interface {intf_s1_s2.name} restored")
        time.sleep(3)
        result_restored = h1.cmd(f"ping -c 3 -W 2 {h2.IP()}")
        loss_restored = [l for l in result_restored.splitlines() if "packet loss" in l]
        print("  After restore:", loss_restored[0].strip() if loss_restored else "no output")

    # Determine pass/fail
    recovery_ok = loss_after and "0% packet loss" in loss_after[0]
    print(f"\n  Recovery Result: {'✓ PASS – Connectivity restored via alternate path' if recovery_ok else '✗ FAIL – Could not recover'}")
    return recovery_ok


def run_iperf_test(net):
    """Run iperf throughput test between h1 and h2."""
    print_banner("IPERF Throughput Test – h1 <-> h2")
    h1 = net.get("h1")
    h2 = net.get("h2")

    # Start iperf server on h2
    h2.cmd("iperf -s &")
    time.sleep(1)

    # Run iperf client on h1
    result = h1.cmd(f"iperf -c {h2.IP()} -t 5")
    print(result)
    h2.cmd("kill %iperf")


def show_flow_tables(net):
    """Dump flow tables from all switches."""
    print_banner("Flow Table Dump (all switches)")
    for sw_name in ["s1", "s2", "s3", "s4"]:
        sw = net.get(sw_name)
        print(f"\n--- {sw_name} ---")
        output = sw.cmd("ovs-ofctl -O OpenFlow10 dump-flows " + sw_name)
        print(output)


def main():
    parser = argparse.ArgumentParser(description="SDN Link Failure Topology")
    parser.add_argument("--test", choices=["scenario1", "scenario2", "both", "none"],
                        default="none",
                        help="Automated test scenario to run (default: none -> interactive CLI)")
    parser.add_argument("--iperf", action="store_true", help="Run iperf test")
    parser.add_argument("--flows", action="store_true", help="Dump flow tables after tests")
    args = parser.parse_args()

    setLogLevel("info")

    net = build_topology()

    try:
        info("*** Starting network\n")
        net.start()
        time.sleep(2)

        info("*** Configuring OpenFlow 1.3 on all switches\n")
        for sw in ["s1", "s2", "s3", "s4"]:
            net.get(sw).cmd(f"ovs-vsctl set bridge {sw} protocols=OpenFlow10")
            net.get(sw).cmd(f"ovs-vsctl set-controller {sw} tcp:{CONTROLLER_IP}:{CONTROLLER_PORT}")

        time.sleep(3)  # allow controller to connect

        if args.test in ("scenario1", "both"):
            scenario1_normal_connectivity(net)

        if args.test in ("scenario2", "both"):
            scenario2_link_failure_recovery(net)

        if args.iperf:
            run_iperf_test(net)

        if args.flows:
            show_flow_tables(net)

        if args.test == "none" or args.test is None:
            print_banner("Interactive CLI – type 'help' for commands")
            CLI(net)

    finally:
        info("*** Stopping network\n")
        net.stop()


if __name__ == "__main__":
    main()
