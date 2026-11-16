"""The registry a publish serves its own build's image tree from.

ecld-build pushes the securelock, the trustedzone and the LAS to the
`localhost:5000` registry of the machine and snapshots that registry's data
as `build/registry`, the image tree the publish uploads and registers. Every
build on the machine replaces what `localhost:5000` serves, so the publish
measures the enclave from the project's own tree instead: it runs a
registry:2 container of the project's own with the tree mounted read-only,
published on the loopback interface at a port free there, and renders the
local compose against that address. The shipped compose keeps
`localhost:5000`, the address the registry answers at on a node. The
container is removed when the publish ends, on success and on failure alike.
"""
import os
import re
import subprocess
import time

import requests

from ethernity_cloud_sdk_py.commands.pynithy.local_kubo import free_port

REGISTRY_IMAGE = "registry:2"
READY_SECONDS = 30
# The repositories ecld-build pushes, as the tree and the composes name them.
REPOSITORIES = ("etny-las", "etny-securelock", "etny-trustedzone")


class LocalRegistry:
    def __init__(self, name, tree):
        self.container = f"ecld-registry-{re.sub(r'[^A-Za-z0-9_.-]', '-', name)}"
        self.tree = tree
        # `127.0.0.1:<port>` once the container runs.
        self.host = None

    def start(self):
        """Remove a container of this name a previous publish left, start one
        over the tree and wait until it answers. Raises when the tree is not a
        build's, docker cannot run the container or it does not answer in
        time, with the container removed; returns True (the spinner marks a
        falsy result failed)."""
        if not os.path.isdir(os.path.join(self.tree, "docker", "registry", "v2")):
            raise Exception(f"{self.tree} holds no image tree; run ecld-build first")
        self.stop()
        port = free_port()
        run = subprocess.run(
            ["docker", "run", "-d", "--name", self.container, "-p", f"127.0.0.1:{port}:5000",
             "-v", f"{self.tree}:/var/lib/registry:ro", REGISTRY_IMAGE],
            capture_output=True, text=True)
        if run.returncode != 0:
            # A run refused a port leaves the created container behind.
            self.stop()
            raise Exception(f"docker could not start {REGISTRY_IMAGE}: {run.stderr.strip()}")
        self.host = f"127.0.0.1:{port}"
        deadline = time.time() + READY_SECONDS
        while True:
            try:
                requests.get(f"http://{self.host}/v2/", timeout=5).raise_for_status()
                return True
            except Exception:
                if time.time() > deadline:
                    self.stop()
                    raise Exception(f"the registry {self.container} did not answer in {READY_SECONDS}s")
                time.sleep(1)

    def image(self, repository):
        """The reference of `repository` in this registry, as the local
        compose names it (`127.0.0.1:<port>/etny-securelock`)."""
        return f"{self.host}/{repository}"

    def stop(self):
        """Remove the container, and the tags the publish pulled from it,
        which name a port no registry answers at any more; nothing when
        docker is not installed."""
        try:
            subprocess.run(["docker", "rm", "-f", self.container], capture_output=True, text=True)
            if self.host is not None:
                subprocess.run(["docker", "rmi"] + [self.image(r) for r in REPOSITORIES], capture_output=True, text=True)
        except OSError:
            pass
