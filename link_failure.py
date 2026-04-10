"""
SDN Link Failure Detection & Recovery - POX Controller
=======================================================
Works with Python 3.13, no extra dependencies needed.

Place this file in ~/pox/ext/link_failure.py
Run with: python3 ~/pox/pox.py link_failure openflow.discovery
"""

from pox.core import core
from pox.lib.util import dpid_to_str
import pox.openflow.libopenflow_01 as of
from pox.lib.revent import *
from pox.lib.addresses import EthAddr
import time

log = core.getLogger()

class LinkFailureController(EventMixin):

    def __init__(self):
        self.mac_to_port = {}       # {dpid: {mac: port}}
        self.adjacency = {}         # {(dpid1, dpid2): (port1, port2)}
        self.switches = {}          # {dpid: connection}
        self.failed_links = []      # log of failures

        # Listen for switch and link events
        core.openflow.addListeners(self)

        # Wait for discovery module
        if core.hasComponent("openflow_discovery"):
            core.openflow_discovery.addListeners(self)
        else:
            def _discovery_ready(event=None):
                core.openflow_discovery.addListeners(self)
            core.call_when_ready(_discovery_ready, "openflow_discovery")

        log.info("=" * 55)
        log.info("  Link Failure Detection & Recovery Controller")
        log.info("  Started: %s", time.strftime("%Y-%m-%d %H:%M:%S"))
        log.info("=" * 55)

    # ------------------------------------------------------------------
    # Switch connects
    # ------------------------------------------------------------------
    def _handle_ConnectionUp(self, event):
        dpid = event.dpid
        self.switches[dpid] = event.connection
        self.mac_to_port.setdefault(dpid, {})
        log.info("[SWITCH UP] %s connected", dpid_to_str(dpid))

        # Install table-miss: send unknown packets to controller
        msg = of.ofp_flow_mod()
        msg.priority = 0
        msg.actions.append(of.ofp_action_output(port=of.OFPP_CONTROLLER))
        event.connection.send(msg)
        log.info("[FLOW] Table-miss rule installed on %s", dpid_to_str(dpid))

    def _handle_ConnectionDown(self, event):
        dpid = event.dpid
        log.warning("[SWITCH DOWN] %s disconnected", dpid_to_str(dpid))
        if dpid in self.switches:
            del self.switches[dpid]

    # ------------------------------------------------------------------
    # Link discovery events
    # ------------------------------------------------------------------
    def _handle_LinkEvent(self, event):
        link = event.link
        src_dpid = link.dpid1
        dst_dpid = link.dpid2
        src_port = link.port1
        dst_port = link.port2

        if event.added:
            self.adjacency[(src_dpid, dst_dpid)] = (src_port, dst_port)
            self.adjacency[(dst_dpid, src_dpid)] = (dst_port, src_port)
            log.info("[LINK UP]   %s port %d <-> %s port %d",
                     dpid_to_str(src_dpid), src_port,
                     dpid_to_str(dst_dpid), dst_port)

        elif event.removed:
            log.warning("=" * 55)
            log.warning("[LINK DOWN] FAILURE DETECTED at %s",
                        time.strftime("%H:%M:%S"))
            log.warning("            %s port %d <-> %s port %d",
                        dpid_to_str(src_dpid), src_port,
                        dpid_to_str(dst_dpid), dst_port)
            log.warning("=" * 55)

            # Log the failure
            self.failed_links.append({
                "time": time.strftime("%H:%M:%S"),
                "src": dpid_to_str(src_dpid),
                "dst": dpid_to_str(dst_dpid),
            })

            # Remove from adjacency
            self.adjacency.pop((src_dpid, dst_dpid), None)
            self.adjacency.pop((dst_dpid, src_dpid), None)

            # Delete stale flows on affected ports
            self._delete_flows_on_port(src_dpid, src_port)
            self._delete_flows_on_port(dst_dpid, dst_port)

            # Clear MAC table so traffic re-learns via new paths
            self.mac_to_port.clear()
            log.info("[RECOVERY] MAC table cleared - re-learning on alternate paths")
            log.info("[RECOVERY] Known links remaining: %d",
                     len(self.adjacency) // 2)

    # ------------------------------------------------------------------
    # Packet-in: learning switch
    # ------------------------------------------------------------------
    def _handle_PacketIn(self, event):
        packet = event.parsed
        if not packet.parsed:
            return

        dpid = event.dpid
        in_port = event.port

        # Ignore LLDP (used by discovery)
        if packet.type == packet.LLDP_TYPE:
            return

        src_mac = str(packet.src)
        dst_mac = str(packet.dst)

        # Learn source MAC
        self.mac_to_port.setdefault(dpid, {})
        if src_mac not in self.mac_to_port[dpid]:
            self.mac_to_port[dpid][src_mac] = in_port
            log.debug("[LEARN] %s: MAC %s on port %d",
                      dpid_to_str(dpid), src_mac, in_port)

        # Decide output port
        if dst_mac in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst_mac]
        else:
            out_port = of.OFPP_FLOOD

        # Install flow rule if destination is known
        if out_port != of.OFPP_FLOOD:
            msg = of.ofp_flow_mod()
            msg.priority = 10
            msg.idle_timeout = 30
            msg.hard_timeout = 120
            msg.match.in_port = in_port
            msg.match.dl_dst = EthAddr(dst_mac)
            msg.actions.append(of.ofp_action_output(port=out_port))
            if dpid in self.switches:
                self.switches[dpid].send(msg)
            log.debug("[FLOW] %s: %s -> port %d",
                      dpid_to_str(dpid), dst_mac, out_port)

        # Send packet out
        msg = of.ofp_packet_out()
        msg.data = event.ofp
        msg.in_port = in_port
        msg.actions.append(of.ofp_action_output(port=out_port))
        if dpid in self.switches:
            self.switches[dpid].send(msg)

    # ------------------------------------------------------------------
    # Port status changes (secondary detection)
    # ------------------------------------------------------------------
    def _handle_PortStatus(self, event):
        port = event.ofp.desc
        if event.added:
            log.info("[PORT] %s port %d ADDED",
                     dpid_to_str(event.dpid), port.port_no)
        elif event.deleted:
            log.warning("[PORT] %s port %d DELETED",
                        dpid_to_str(event.dpid), port.port_no)
        elif event.modified:
            if port.state & of.OFPPS_LINK_DOWN:
                log.warning("[PORT] %s port %d LINK DOWN",
                            dpid_to_str(event.dpid), port.port_no)
            else:
                log.info("[PORT] %s port %d LINK UP",
                         dpid_to_str(event.dpid), port.port_no)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _delete_flows_on_port(self, dpid, port):
        """Delete all flows that use a specific input port."""
        if dpid not in self.switches:
            return
        msg = of.ofp_flow_mod()
        msg.command = of.OFPFC_DELETE
        msg.match.in_port = port
        msg.out_port = of.OFPP_NONE
        self.switches[dpid].send(msg)
        log.info("[FLOW DELETE] %s: removed flows on port %d",
                 dpid_to_str(dpid), port)


def launch():
    core.registerNew(LinkFailureController)
    log.info("Link Failure Controller loaded.")
