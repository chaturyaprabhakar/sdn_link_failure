set -e

CONTROLLER="controller.py"
TOPOLOGY="topology.py"
RYU_LOG="ryu_controller.log"
TEST_ARG="${1:-}"

# ---------- cleanup ----------
cleanup() {
    echo ""
    echo "[*] Cleaning up..."
    sudo mn --clean 2>/dev/null || true
    pkill -f "ryu-manager" 2>/dev/null || true
    sleep 1
    echo "[*] Done."
}

if [ "$TEST_ARG" = "--clean" ]; then
    cleanup
    exit 0
fi

# ---------- preflight ----------
echo "============================================================"
echo "  SDN Link Failure Detection & Recovery"
echo "============================================================"

if ! command -v ryu-manager &>/dev/null; then
    echo "[ERROR] ryu-manager not found. Install with: pip install ryu"
    exit 1
fi

if ! command -v mn &>/dev/null; then
    echo "[ERROR] mininet not found. Install with: sudo apt install mininet"
    exit 1
fi

# Cleanup any leftover state
sudo mn --clean 2>/dev/null || true

# ---------- start Ryu controller ----------
echo "[*] Starting Ryu controller (log -> $RYU_LOG)..."
ryu-manager --observe-links "$CONTROLLER" > "$RYU_LOG" 2>&1 &
RYU_PID=$!
echo "    PID: $RYU_PID"
sleep 3

if ! kill -0 "$RYU_PID" 2>/dev/null; then
    echo "[ERROR] Ryu controller failed to start. Check $RYU_LOG"
    exit 1
fi
echo "[*] Ryu controller running."

# ---------- start Mininet ----------
echo "[*] Starting Mininet topology..."
if [ "$TEST_ARG" = "--test" ] && [ -n "$2" ]; then
    sudo python3 "$TOPOLOGY" --test "$2" --flows
elif [ "$TEST_ARG" = "--iperf" ]; then
    sudo python3 "$TOPOLOGY" --iperf --flows
else
    sudo python3 "$TOPOLOGY"
fi

# ---------- teardown ----------
cleanup
