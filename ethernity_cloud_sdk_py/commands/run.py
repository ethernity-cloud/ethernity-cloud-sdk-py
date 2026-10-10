"""ecld-run -- submit a payload to the Ethernity Cloud network and print the result.

The network-side sibling of `ecld-test`: same payload flags (a code string,
--file, --input/--input-text), but instead of executing locally it drives the
real runner (ethernity_cloud_runner_py.EthernityCloudRunner) end to end --
encrypt the payload, push to IPFS, place the on-chain DO request, wait for a
node to process it, download and decrypt the result.

Where ecld-test answers "does my CALL work?", ecld-run answers "does it work on
the network, on the enclave I published?" -- it costs gas and needs a funded
key.

Network and enclave come from the project's .config.json (falling back to
.env), exactly as `ecld-publish` wired them:

    BLOCKCHAIN_NETWORK   e.g. "BLOXBERG_TESTNET" -> network name + type
    PROJECT_NAME         the securelock enclave to run
    TRUSTED_ZONE_IMAGE   the trustedzone enclave

The signing key is resolved like ecld-test's caller, but here it must be a real
funded key (it pays for the order):

    1. ECLD_PRIVATE_KEY            -- a plaintext 0x-prefixed key (dev convenience)
    2. ENC_PRIVATE_KEY (.config/.env) -- encrypted; decrypted with ECLD_KEY_PASSWORD
                                     or an interactive prompt

Usage, from the project root (where .config.json lives):

    ecld-run 'hello("World")'
    ecld-run --file payload.py
    ecld-run --input data.json 'process(___etny_data_set___)'
    ecld-run --network BLOXBERG_TESTNET --task-price 3 'esr_increment()'
    ecld-run --json 'hello("World")'      # machine-readable result

Exit code 0 on a SUCCESS task result, 1 otherwise.
"""

import argparse
import json
import os
import sys
import time


# The runner queries the image registry with the PROTOCOL version ("v3"), not
# the enclave/template VERSION. See ethernity_task.py -- passing the template
# version resolves a stale/missing image. This is a fixed constant.
SECURELOCK_PROTOCOL_VERSION = "v3"

# `--ipfs intake` (the default): no IPFS API of our own; artefact CIDs are
# computed locally and the bytes are delivered to the bootnode's payload intake
# once the DO request is on chain (runner set_public_intake). `--ipfs <url>`
# uploads and reads through that Kubo RPC API instead; the public API's add is
# closed to callers without a token.
INTAKE_KEYWORD = "intake"
DEFAULT_IPFS = INTAKE_KEYWORD


def _load_config():
    """Return a Config over ./.config.json, or None if it cannot be read."""
    try:
        from ethernity_cloud_sdk_py.commands.config import Config
        cfg = Config(".config.json")
        cfg.load()
        return cfg
    except Exception:
        return None


def _cfg_or_env(cfg, key, env_key=None):
    """Prefer .config.json, then the matching .env / process-env value."""
    if cfg is not None:
        val = cfg.read(key)
        if val not in (None, ""):
            return str(val)
    return os.environ.get(env_key or key)


def _network_member(explicit, cfg, unsafe=False):
    """The BlockchainNetworks member named by --network, else by
    BLOCKCHAIN_NETWORK, or None when neither names one. With `unsafe`, its
    -unsafe variant (<NAME>_UNSAFE); a network without one raises ValueError."""
    from ethernity_cloud_sdk_py.commands.enums import BlockchainNetworks
    raw = (explicit or _cfg_or_env(cfg, "BLOCKCHAIN_NETWORK") or "").strip().upper()
    if unsafe and raw and not raw.endswith("_UNSAFE"):
        raw = f"{raw}_UNSAFE"
        if raw not in BlockchainNetworks.__members__:
            raise ValueError(f"{raw[:-len('_UNSAFE')]} has no -unsafe variant")
    if raw in BlockchainNetworks.__members__:
        return BlockchainNetworks[raw]
    return None


def _trustedzone_for(member, cfg):
    """The trustedzone image of `member` for the project's dApp type, or None."""
    if member is None:
        return None
    template = member.template_image.get(_cfg_or_env(cfg, "DAPP_TYPE") or "Pynithy")
    return template.trusted_zone_image if template else None


def _resolve_network(explicit, cfg, member=None):
    """(network_name, network_type) from the network member when one is known
    (--network, else BLOCKCHAIN_NETWORK), else NETWORK_NAME/NETWORK_TYPE in the
    env, else the Bloxberg testnet.

    A member is the runner's network it maps to (RUNNER_NETWORKS):
    BLOXBERG_TESTNET_UNSAFE is the runner's BLOXBERG TESTNET_UNSAFE, which runs
    only -unsafe trustedzones."""
    if member is None:
        member = _network_member(explicit, cfg)
    if member is not None:
        return member.runner_network
    raw = explicit or _cfg_or_env(cfg, "BLOCKCHAIN_NETWORK")
    if raw and "_" in raw:
        name, _, ntype = raw.partition("_")
        return name.upper(), ntype.upper()
    name = os.environ.get("NETWORK_NAME")
    ntype = os.environ.get("NETWORK_TYPE")
    if name and ntype:
        return name.upper(), ntype.upper()
    return "BLOXBERG", "TESTNET"


def _resolve_private_key(cfg):
    """A real, funded signing key (it pays for the order). Resolution:

      1. ECLD_PRIVATE_KEY -- a plaintext 0x key (dev convenience).
      2. ENC_PRIVATE_KEY (.config.json / .env) -- decrypted with ECLD_KEY_PASSWORD,
         else an interactive password prompt.

    Returns a 0x-prefixed key string, or raises RuntimeError with a clear reason.
    """
    priv = os.environ.get("ECLD_PRIVATE_KEY", "").strip()
    if priv:
        return priv if priv.startswith("0x") else "0x" + priv

    enc = _cfg_or_env(cfg, "ENC_PRIVATE_KEY")
    if not enc:
        raise RuntimeError(
            "no signing key found. Set ECLD_PRIVATE_KEY to a funded 0x key, or "
            "run `ecld-publish` first so ENC_PRIVATE_KEY is stored, then set "
            "ECLD_KEY_PASSWORD (or enter it when prompted)."
        )

    pwd = os.environ.get("ECLD_KEY_PASSWORD")
    if pwd is None:
        try:
            import getpass
            pwd = getpass.getpass("Private key password (to sign the order): ")
        except Exception:
            raise RuntimeError(
                "ENC_PRIVATE_KEY is set but no password is available "
                "(set ECLD_KEY_PASSWORD)."
            )
    try:
        from ethernity_cloud_sdk_py.commands.private_key import PrivateKeyManager
        return PrivateKeyManager(pwd).decrypt_private_key(enc)
    except Exception as e:
        raise RuntimeError(f"could not decrypt ENC_PRIVATE_KEY (wrong password?): {e}")


def _build_resources(args):
    """The resources object run() expects; --flags override the defaults."""
    return {
        "taskPrice": args.task_price,
        "cpu": args.cpu,
        "memory": args.memory,
        "storage": args.storage,
        "bandwidth": args.bandwidth,
        "duration": args.duration,
        "validators": args.validators,
    }


def _print_progress(runner, timeout):
    """Poll the runner, printing each new progress phase, until it stops or
    the timeout elapses. Returns the final state dict."""
    seen = None
    started = time.monotonic()
    while runner.is_running():
        state = runner.get_state()
        phase = state.get("progress")
        if phase != seen:
            seen = phase
            status = state.get("status")
            print(f"[{phase}] {status}")
        if timeout and (time.monotonic() - started) > timeout:
            print(f"[timeout] no result after {timeout}s; the order may still "
                  f"process on-chain. Check later with the runner.", file=sys.stderr)
            break
        time.sleep(0.5)
    return runner.get_state()


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="ecld-run",
        description="Submit a payload to the Ethernity Cloud network and print the result "
                    "(costs gas; needs a funded key). Local sibling: ecld-test.",
    )
    parser.add_argument("code", nargs="*", help="payload code string, e.g. 'hello(\"World\")'")
    parser.add_argument("--file", "-f", help="read the payload from a file instead")
    parser.add_argument("--input", "-i", dest="input_file",
                        help="file whose content becomes ___etny_data_set___")
    parser.add_argument("--input-text", dest="input_text",
                        help="literal string for ___etny_data_set___")
    parser.add_argument("--network",
                        help="override the network, e.g. BLOXBERG_TESTNET "
                             "(default: BLOCKCHAIN_NETWORK from .config.json)")
    parser.add_argument("--node", default="", help="target a specific node operator address")
    parser.add_argument("--unsafe", action="store_true",
                        help="run the -unsafe variant of the network: <PROJECT_NAME>-unsafe "
                             "on its -unsafe trustedzone, without a CAS")
    parser.add_argument("--securelock",
                        help="securelock enclave name (default: PROJECT_NAME, "
                             "<PROJECT_NAME>-unsafe on an -unsafe network)")
    parser.add_argument("--trustedzone",
                        help="trustedzone enclave name (default: the network's, "
                             "else TRUSTED_ZONE_IMAGE)")
    parser.add_argument("--ipfs", default=DEFAULT_IPFS,
                        help=f"'{INTAKE_KEYWORD}' (default) delivers the task's artefacts through the "
                             f"bootnode's payload intake; a Kubo RPC API URL uploads and reads through "
                             f"that node instead")
    parser.add_argument("--timeout", type=int, default=600,
                        help="seconds to wait for a result before giving up (default: 600)")
    parser.add_argument("--log-level", default="ERROR",
                        help="runner log level: ERROR|WARNING|INFO|DEBUG (default: ERROR)")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    # Resource flags (run()'s resources object).
    parser.add_argument("--task-price", type=int, default=3, help="price in tokens (default: 3)")
    parser.add_argument("--cpu", type=int, default=1)
    parser.add_argument("--memory", type=int, default=1)
    parser.add_argument("--storage", type=int, default=10)
    parser.add_argument("--bandwidth", type=int, default=1)
    parser.add_argument("--duration", type=int, default=1)
    parser.add_argument("--validators", type=int, default=1)
    args = parser.parse_args(argv)

    # dotenv, if present, so ENC_PRIVATE_KEY / NETWORK_* are available.
    try:
        from dotenv import load_dotenv
        load_dotenv(override=False)
    except Exception:
        pass

    # ---- payload ----
    env = os.environ.get
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            code = fh.read()
    elif args.code:
        code = " ".join(args.code)
    elif env("ECLD_TEST_CODE", "").strip():
        code = env("ECLD_TEST_CODE").strip()
    else:
        parser.error("provide a payload string, --file, or ECLD_TEST_CODE")

    if args.input_file is not None and args.input_text is not None:
        parser.error("--input and --input-text are mutually exclusive")
    input_data = None
    if args.input_file is not None:
        with open(args.input_file, encoding="utf-8") as fh:
            input_data = fh.read()
    elif args.input_text is not None:
        input_data = args.input_text

    # ---- config / network / enclaves / key ----
    cfg = _load_config()
    try:
        member = _network_member(args.network, cfg, unsafe=args.unsafe)
    except ValueError as e:
        parser.error(str(e))
    network_name, network_type = _resolve_network(args.network, cfg, member)
    project = _cfg_or_env(cfg, "PROJECT_NAME")
    securelock = args.securelock or (
        member.securelock_name(project) if (member is not None and project) else project)
    trustedzone = (args.trustedzone or _trustedzone_for(member, cfg)
                   or _cfg_or_env(cfg, "TRUSTED_ZONE_IMAGE"))

    if not securelock:
        parser.error("no securelock enclave: set PROJECT_NAME in .config.json "
                     "(run ecld-init/ecld-publish) or pass --securelock.")
    if not trustedzone:
        # Fall back to run()'s own default trustedzone (etny-pynithy-testnet).
        # Never guess it from the securelock name -- that resolves a wrong image.
        trustedzone = None

    try:
        private_key = _resolve_private_key(cfg)
    except RuntimeError as e:
        print(f"ecld-run: {e}", file=sys.stderr)
        return 1

    print(f"network   : {network_name}_{network_type}")
    print(f"securelock: {securelock}  (protocol {SECURELOCK_PROTOCOL_VERSION})")
    print(f"trustedzone: {trustedzone if trustedzone else '(runner default)'}")
    print(f"payload   : {code!r}")
    print(f"input     : {'<empty>' if input_data is None else repr(input_data[:80])}")

    # ---- drive the runner ----
    try:
        from ethernity_cloud_runner_py.runner import EthernityCloudRunner
    except Exception as e:
        print(f"ecld-run: the runner package is not installed "
              f"(pip install ethernity-cloud-runner-py): {e}", file=sys.stderr)
        return 1

    try:
        runner = EthernityCloudRunner(network_name, network_type)
        try:
            runner.set_log_level(args.log_level)
        except Exception:
            pass
        runner.set_private_key(private_key)
        if args.ipfs == INTAKE_KEYWORD:
            runner.set_public_intake()
        else:
            runner.set_storage_ipfs(args.ipfs)
        runner.connect()

        resources = _build_resources(args)
        print(f"resources : {resources}")
        print("submitting order... (this places an on-chain request and costs gas)")

        run_args = [resources, securelock, SECURELOCK_PROTOCOL_VERSION, code, args.node]
        if trustedzone:
            run_args.append(trustedzone)
        runner.run(*run_args)

        state = _print_progress(runner, args.timeout)
    except KeyboardInterrupt:
        print("\necld-run: interrupted; the order may still be processing on-chain.",
              file=sys.stderr)
        return 130
    except Exception as e:
        print(f"ecld-run: run failed: {e}", file=sys.stderr)
        return 1

    status = state.get("status")
    if status == "SUCCESS":
        result = runner.get_result() or {}
        # The order this result came from, so the run can be followed on
        # chain: the CAS validators' verdict is keyed by order id.
        if isinstance(result, dict):
            result["order_id"] = runner.order_id
            result["do_request_id"] = runner.do_request_id
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            value = result.get("value") if isinstance(result, dict) else result
            code_str = result.get("task_code_string") if isinstance(result, dict) else None
            if code_str and code_str != "SUCCESS":
                print(f"task code : {result.get('task_code_int')} ({code_str})")
            print(f"order     : {runner.order_id} (DO request {runner.do_request_id})")
            print("result    :")
            print(value)
        # A non-SUCCESS enclave task code is a failed run even though the order
        # itself succeeded (the enclave reported an error as the result).
        tc = result.get("task_code_int") if isinstance(result, dict) else None
        return 0 if tc in (None, 0) else 1

    # ERROR / timeout
    err = state.get("last_error") or "unknown error"
    print(f"status    : {status}", file=sys.stderr)
    print(f"error     : {err}", file=sys.stderr)
    proc = state.get("processed_events")
    rem = state.get("remaining_events")
    if proc or rem:
        print(f"processed : {proc}  remaining: {rem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
