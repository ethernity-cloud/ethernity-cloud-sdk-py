"""The Kubo a publish runs for its own upload.

An image is published by adding it to IPFS and registering its CID. A
publish without an IPFS endpoint of its own runs a Kubo in docker (the SDK
already requires docker), adds the image through that Kubo's HTTP API (the
add's parameters fix the CIDs, so a build gives the same CIDs on any Kubo),
peers the Kubo with the bootnode, whose mirror pins every registered image
and from which the certificate extraction service fetches, and releases it
when the publish ends (the finally of publish.main; `exit_on_interrupt` makes
a SIGTERM or SIGHUP reach it as Ctrl+C does). The release removes the Kubo,
unless the publish ends with the image registered and its certificate not on
chain: the registry entry names this Kubo's peer id as the image's source, so
the Kubo keeps running and the next publish of the project takes it over
(starting it again when a restart of the docker daemon stopped it) and keeps it
in turn until the certificate is on chain. The Kubo listens for the API on the
loopback interface only; its swarm port is published when free, so a publisher
with a reachable address is also dialed directly, and left unpublished when
another container holds it, the Kubo then being reached over the connection it
opens to the bootnode.
"""
import os
import re
import signal
import socket
import subprocess
import time

import requests
from urllib.parse import urlparse

KUBO_IMAGE = os.environ.get("ECLD_KUBO_IMAGE", "ipfs/kubo:release")
# The bootnode's IPFS node (ipfs.ethernity.cloud), as the node agents peer with it.
BOOTNODE_MULTIADDR = "/dns4/ipfs.ethernity.cloud/tcp/4001/p2p/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5"
SWARM_PORT = int(os.environ.get("ECLD_KUBO_SWARM_PORT", "4001"))
API_READY_SECONDS = 90
# The public IPFS API: an IPFS_ENDPOINT naming it is not an endpoint of the
# application's own, so the publish runs its own Kubo instead. It takes a pin
# with a token (publish.py `_pin_on_public_api`).
PUBLIC_IPFS_HOST = "ipfs.ethernity.cloud"
PUBLIC_IPFS_API = f"https://{PUBLIC_IPFS_HOST}"
# What docker's refusal of a `docker run` says when a published port is held
# by another container.
PORT_ALLOCATED = "port is already allocated"

_PRIVATE = re.compile(r"^/ip4/(127\.|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.|0\.0\.0\.0|169\.254\.)|^/ip6/(::1|fe80|fc|fd)")


def own_endpoint(endpoint):
    """Whether `endpoint` is an IPFS API of the application's own: set, and
    not the public one. An empty setting and the public API both mean the
    publish runs a Kubo of its own."""
    return bool(endpoint) and urlparse(endpoint).hostname != PUBLIC_IPFS_HOST


def api_port_in(docker_port_output):
    """The host port in the first line of `docker port <container> 5001/tcp`
    (`127.0.0.1:60823`), or None when there is none."""
    first = (docker_port_output or "").strip().splitlines()
    match = re.search(r":(\d+)$", first[0].strip()) if first else None
    return int(match.group(1)) if match else None


def free_port():
    """A port free on the loopback interface, for a container port published
    there: the daemon reaches it under Docker Desktop too, where the daemon
    runs in a VM and a host port the daemon assigns (`-p 127.0.0.1::<port>`)
    is bound on the host side alone."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_free(port):
    with socket.socket() as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def exit_on_interrupt():
    """Make a SIGTERM, and a SIGHUP where the platform has one, end the
    publish as Ctrl+C does: the handler raises KeyboardInterrupt, which the
    finally of publish.main sees (the registry is removed and the Kubo
    released there). Python installs no handler for either signal, so the
    process would otherwise end without reaching it."""
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    for name in ("SIGTERM", "SIGHUP"):
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, interrupted)


class LocalKubo:
    def __init__(self, name):
        self.container = f"ecld-kubo-{re.sub(r'[^A-Za-z0-9_.-]', '-', name)}"
        self.api_port = free_port()
        self.api_url = f"http://127.0.0.1:{self.api_port}"
        self.peers = [p.strip() for p in os.environ.get("ECLD_IPFS_PEERS", BOOTNODE_MULTIADDR).split(",") if p.strip()]
        # True while a registered image has this Kubo as its source: from the
        # image's registration on chain, or from the takeover of the Kubo a
        # previous publish kept (`start`), until the certificate is on chain.
        # A publish that ends in between leaves the Kubo running (`release`).
        self.serving = False

    def _api(self, command, params=None, timeout=30):
        response = requests.post(f"{self.api_url}/api/v0/{command}", params=params or {}, timeout=timeout)
        response.raise_for_status()
        return response.json() if response.text.strip() else {}

    def kept_api_port(self):
        """The API port of the container a previous publish of this project
        left running (`release`), or None when none runs: `docker port` fails
        on a stopped container and on none."""
        run = subprocess.run(["docker", "port", self.container, "5001/tcp"], capture_output=True, text=True)
        return api_port_in(run.stdout) if run.returncode == 0 else None

    def start_kept(self):
        """Start the stopped container a previous publish of this project
        left (the Kubo runs with no restart policy, so a restart of the docker
        daemon stops it); False when there is none docker can start."""
        run = subprocess.run(["docker", "start", self.container], capture_output=True, text=True)
        return run.returncode == 0

    def _run(self, publish_swarm_port):
        """`docker run` the container, with the swarm port published when
        `publish_swarm_port`. A run docker refuses because that port is
        already allocated to another container is made again without it.
        Raises, with the created container removed, when docker refuses the
        run."""
        command = ["docker", "run", "-d", "--name", self.container,
                   "-p", f"127.0.0.1:{self.api_port}:5001"]
        if publish_swarm_port:
            command += ["-p", f"{SWARM_PORT}:4001", "-p", f"{SWARM_PORT}:4001/udp"]
        command += [KUBO_IMAGE, "daemon", "--init", "--migrate=true"]
        run = subprocess.run(command, capture_output=True, text=True)
        if run.returncode == 0:
            return
        # A run refused a port leaves the created container behind.
        self.stop()
        if publish_swarm_port and PORT_ALLOCATED in run.stderr:
            print(f"\t⚠  swarm port {SWARM_PORT} is allocated to another container; the IPFS node is not dialed directly")
            self._run(False)
            return
        raise Exception(f"docker could not start {KUBO_IMAGE}: {run.stderr.strip()}")

    def start(self):
        """Start the container and wait for its API, or take over the one a
        previous publish of this project left serving its registered image,
        whose peer id that image's registry entry names, which this publish
        then serves too (`serving`, until the certificate it writes is on
        chain), started again when a restart of the docker daemon stopped it
        (`start_kept`); peer it with the bootnode. Raises when docker cannot
        run it or the API does not come up in time, with a container started
        here removed and a kept one left for `release`; returns True (the
        spinner marks a falsy result failed)."""
        kept = self.kept_api_port()
        if kept is None and self.start_kept():
            kept = self.kept_api_port()
        if kept is not None:
            self.api_port = kept
            self.api_url = f"http://127.0.0.1:{kept}"
            # The container runs because it serves a registered image whose
            # certificate is not on chain yet (`release`).
            self.serving = True
            print(f"\t✔  IPFS node of the previous publish kept: {self.container}")
        else:
            self.stop()
            self._run(_port_free(SWARM_PORT))
        deadline = time.time() + API_READY_SECONDS
        while True:
            try:
                self._api("id", timeout=5)
                break
            except Exception:
                if time.time() > deadline:
                    if not self.serving:
                        self.stop()
                    raise Exception(f"the Kubo API did not come up in {API_READY_SECONDS}s")
                time.sleep(2)
        # The peering service dials each peer and keeps the connection; the
        # connect is waited for, not forced, since the service's own dial and
        # a swarm/connect of the same peer refuse each other.
        for peer in self.peers:
            try:
                self._api("swarm/peering/add", {"arg": peer}, timeout=15)
            except Exception as e:
                print(f"\t⚠  could not peer with {peer}: {e}")
                continue
            deadline = time.time() + 30
            while not self.connected_to(peer) and time.time() < deadline:
                time.sleep(1)
            if not self.connected_to(peer):
                print(f"\t⚠  not connected to {peer} yet; the peering service keeps trying")
        return True

    def peer_multiaddr(self):
        """How this Kubo is registered on chain: its first public address
        with the peer id, or `/p2p/<id>` when it has none a stranger could
        dial (the bootnode reaches it over the connection it opened)."""
        identity = self._api("id")
        peer_id = identity["ID"]
        for address in identity.get("Addresses") or []:
            if not _PRIVATE.search(address) and "/p2p-circuit" not in address and f"/p2p/{peer_id}" in address:
                return address
        return f"/p2p/{peer_id}"

    def provide(self, cid):
        """Announce `cid` to the DHT so a node not connected to this one can
        find it. Best effort."""
        try:
            requests.post(f"{self.api_url}/api/v0/routing/provide", params={"arg": cid}, timeout=180).close()
        except Exception as e:
            print(f"\t⚠  could not announce {cid}: {e}")

    def connected_to(self, peer_multiaddr):
        """Whether the swarm holds a connection to the peer of `peer_multiaddr`."""
        peer_id = peer_multiaddr.rsplit("/p2p/", 1)[-1]
        try:
            peers = self._api("swarm/peers").get("Peers") or []
        except Exception:
            return False
        return any(p.get("Peer") == peer_id for p in peers)

    def stop(self):
        """Remove the container and its anonymous volumes (`-v`); nothing
        when docker is not installed. The Kubo image declares /data/ipfs and
        /export as volumes, and /data/ipfs holds the repo with every block
        the publish added. A container run afresh gets new anonymous volumes,
        so no container reads these again."""
        try:
            subprocess.run(["docker", "rm", "-f", "-v", self.container], capture_output=True, text=True)
        except OSError:
            pass

    def release(self):
        """End the publish's use of the Kubo: stop it, unless it is the source
        of a registered image whose certificate is not on chain yet, which it
        keeps serving to the extraction service and the bootnode's mirror;
        the next publish of the project takes it over, `docker rm -f -v`
        stops it."""
        if not self.serving:
            self.stop()
            return
        print(f"\t⚠  The registered image stays available from this publish's IPFS node, the docker")
        print(f"\t   container {self.container}, for the extraction service and the bootnode.")
        print(f"\t   Publish again to retry; `docker rm -f -v {self.container}` stops it.")
