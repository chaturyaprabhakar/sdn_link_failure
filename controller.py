from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types, lldp
from ryu.topology import event as topo_event
from ryu.topology.api import get_switch, get_link
from ryu.app import simple_switch_13
import networkx as nx
import logging
import time

LOG = logging.getLogger('LinkFailureController')
LOG.setLevel(logging.DEBUG)

# Console handler with clear formatting
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
ch.setFormatter(formatter)
LOG.addHandler(ch)


class LinkFailureController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    # Tell Ryu to also load topology discovery
    _CONTEXTS = {}

    def __init__(self, *args, **kwargs):
        super(LinkFailureController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}          # {dpid: {mac: port}}
        self.topology_graph = nx.Graph()  # network graph
        self.datapaths = {}            # {dpid: datapath}
        self.link_to_port = {}         # {(src_dpid, dst_dpid): (src_port, dst_port)}
        self.failure_log = []          # log of failure events

        LOG.info("=" * 60)
        LOG.info("  SDN Link Failure Detection & Recovery Controller")
        LOG.info("  Started at %s", time.strftime("%Y-%m-%d %H:%M:%S"))
        LOG.info("=" * 60)

    # Switch lifecycle

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        dpid = datapath.id

        self.datapaths[dpid] = datapath
        LOG.info("[SWITCH CONNECT] DPID %016x connected", dpid)

        # Install table-miss flow: send unknown packets to controller
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self._add_flow(datapath, priority=0, match=match, actions=actions)
        LOG.info("[FLOW] Table-miss rule installed on DPID %016x", dpid)

    # Topology events (requires --observe-links flag or topology app)

    @set_ev_cls(topo_event.EventSwitchEnter)
    def switch_enter_handler(self, ev):
        switch = ev.switch
        dpid = switch.dp.id
        self.topology_graph.add_node(dpid)
        LOG.info("[TOPOLOGY] Switch %016x added to graph (nodes: %d)",
                 dpid, self.topology_graph.number_of_nodes())

    @set_ev_cls(topo_event.EventSwitchLeave)
    def switch_leave_handler(self, ev):
        switch = ev.switch
        dpid = switch.dp.id
        self.topology_graph.remove_node(dpid)
        LOG.warning("[TOPOLOGY] Switch %016x removed from graph", dpid)

    @set_ev_cls(topo_event.EventLinkAdd)
    def link_add_handler(self, ev):
        link = ev.link
        src = link.src
        dst = link.dst
        self.topology_graph.add_edge(src.dpid, dst.dpid)
        self.link_to_port[(src.dpid, dst.dpid)] = (src.port_no, dst.port_no)
        LOG.info("[LINK UP]   %016x (port %d) <--> %016x (port %d)",
                 src.dpid, src.port_no, dst.dpid, dst.port_no)

    @set_ev_cls(topo_event.EventLinkDelete)
    def link_delete_handler(self, ev):
        """
        Called when a link goes down.
        Steps:
          1. Log the failure
          2. Remove from graph
          3. Delete stale flow rules on affected switches
          4. Recompute paths and install new rules
        """
        link = ev.link
        src_dpid = link.src.dpid
        dst_dpid = link.dst.dpid
        src_port = link.src.port_no
        dst_port = link.dst.port_no

        ts = time.strftime("%H:%M:%S")
        LOG.warning("=" * 60)
        LOG.warning("[LINK DOWN] FAILURE DETECTED at %s", ts)
        LOG.warning("            %016x (port %d) <--> %016x (port %d)",
                    src_dpid, src_port, dst_dpid, dst_port)
        LOG.warning("=" * 60)

        # Record failure
        self.failure_log.append({
            "time": ts,
            "src_dpid": src_dpid,
            "dst_dpid": dst_dpid,
            "src_port": src_port,
            "dst_port": dst_port,
        })

        # Remove edge from graph
        if self.topology_graph.has_edge(src_dpid, dst_dpid):
            self.topology_graph.remove_edge(src_dpid, dst_dpid)
        self.link_to_port.pop((src_dpid, dst_dpid), None)
        self.link_to_port.pop((dst_dpid, src_dpid), None)

        # Delete flows that used the failed port on both switches
        for dpid, port in [(src_dpid, src_port), (dst_dpid, dst_port)]:
            if dpid in self.datapaths:
                self._delete_flows_on_port(self.datapaths[dpid], port)
                LOG.info("[RECOVERY] Deleted stale flows on DPID %016x port %d", dpid, port)

        # Clear MAC table so re-learning happens with new topology
        self.mac_to_port.clear()
        LOG.info("[RECOVERY] MAC table cleared – re-learning will use alternate paths")
        LOG.info("[RECOVERY] Topology now has %d edges", self.topology_graph.number_of_edges())

    # Packet-in handler (learning switch logic)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']
        dpid = datapath.id

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        # Ignore LLDP (used for topology discovery)
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dst_mac = eth.dst
        src_mac = eth.src

        # Learn MAC address
        self.mac_to_port.setdefault(dpid, {})
        if src_mac not in self.mac_to_port[dpid]:
            self.mac_to_port[dpid][src_mac] = in_port
            LOG.debug("[LEARN] DPID %016x: MAC %s on port %d", dpid, src_mac, in_port)

        # Decide output port
        if dst_mac in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst_mac]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # Install a flow rule if we know the destination
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst_mac)
            self._add_flow(datapath, priority=10, match=match, actions=actions,
                           idle_timeout=30, hard_timeout=120)
            LOG.debug("[FLOW] Installed: DPID %016x  %s -> port %d",
                      dpid, dst_mac, out_port)

        # Send packet out
        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data
        out = parser.OFPPacketOut(datapath=datapath,
                                   buffer_id=msg.buffer_id,
                                   in_port=in_port,
                                   actions=actions,
                                   data=data)
        datapath.send_msg(out)

    # Port status change (secondary detection mechanism)

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def port_status_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofproto = dp.ofproto
        reason = msg.reason
        port_no = msg.desc.port_no

        reason_map = {
            ofproto.OFPPR_ADD:    "PORT ADDED",
            ofproto.OFPPR_DELETE: "PORT DELETED",
            ofproto.OFPPR_MODIFY: "PORT MODIFIED",
        }
        reason_str = reason_map.get(reason, "UNKNOWN")

        if reason == ofproto.OFPPR_MODIFY:
            state = msg.desc.state
            if state & ofproto.OFPPS_LINK_DOWN:
                LOG.warning("[PORT STATUS] DPID %016x port %d LINK DOWN", dp.id, port_no)
            else:
                LOG.info("[PORT STATUS] DPID %016x port %d LINK UP", dp.id, port_no)
        else:
            LOG.info("[PORT STATUS] DPID %016x port %d %s", dp.id, port_no, reason_str)

    # Flow stats (periodic monitoring)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        LOG.info("[STATS] DPID %016x – %d flow entries:", dpid, len(body))
        for stat in sorted(body, key=lambda s: s.priority, reverse=True):
            if stat.priority == 0:
                continue  # skip table-miss
            LOG.info("  match=%s  actions=%s  packets=%d  bytes=%d",
                     stat.match, stat.instructions, stat.packet_count, stat.byte_count)

    def request_flow_stats(self, datapath):
        parser = datapath.ofproto_parser
        req = parser.OFPFlowStatsRequest(datapath)
        datapath.send_msg(req)

    # Helpers

    def _add_flow(self, datapath, priority, match, actions,
                  idle_timeout=0, hard_timeout=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath,
                                 priority=priority,
                                 match=match,
                                 instructions=inst,
                                 idle_timeout=idle_timeout,
                                 hard_timeout=hard_timeout)
        datapath.send_msg(mod)

    def _delete_flows_on_port(self, datapath, port):
        """Delete all flows that output to a specific port."""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        # Match any flow with output action to this port
        match = parser.OFPMatch()
        mod = parser.OFPFlowMod(
            datapath=datapath,
            command=ofproto.OFPFC_DELETE,
            out_port=port,
            out_group=ofproto.OFPG_ANY,
            match=match,
        )
        datapath.send_msg(mod)
