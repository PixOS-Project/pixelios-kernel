#!/bin/sh
# Print the one root command that gives the phone's USB Ethernet link a host address.
#   tools/sweet-usbnet.sh --stage
# The command is only printed, never run here: the user runs it once the phone has enumerated.
# Live log capture is tools/usbnet-log.py (root-free, started by tools/xrun).
set -eu
PHONE_MAC=02:00:5e:77:ee:01	# host-side MAC of the phone's CDC-ECM function
HOST_ADDR=192.168.7.1/24	# the phone is 192.168.7.2
if [ "${1:-}" != "--stage" ]; then
	echo "usage: $0 --stage" >&2
	echo "RESULT: ERROR need --stage" >&2
	exit 2
fi
# The interface is found by MAC when the line runs, so it can be staged before the phone enumerates.
echo "sudo sh -c 'i=\$(ip -o link | grep -i $PHONE_MAC | cut -d: -f2 | cut -d@ -f1 | xargs); ip addr replace $HOST_ADDR dev \$i && ip link set \$i up'"
echo "RESULT: PASS staged" >&2
exit 0
