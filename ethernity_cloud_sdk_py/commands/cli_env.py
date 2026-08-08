"""Shared unattended-CLI resolution helpers (ESR RFC §9).

Resolution precedence everywhere: CLI flag -> env var -> .config.json ->
interactive prompt. Prompts fire only when a real TTY is attached and
non-interactive mode is off; in non-interactive mode a missing required value
is a hard error (exit 2), never a hang.
"""

import os
import sys

TRUTHY = ("1", "true", "yes", "y", "on")


def env_str(name, default=None):
    v = os.environ.get(name)
    if v is None:
        return default
    v = v.strip()
    return v if v != "" else default


def env_bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in TRUTHY


def non_interactive():
    """True when prompting is impossible or explicitly disabled."""
    if env_bool("ECLD_NON_INTERACTIVE"):
        return True
    try:
        return not sys.stdin.isatty()
    except Exception:
        return True


def assume_yes():
    return env_bool("ECLD_ASSUME_YES")


def die(message, code=2):
    print(f"ERROR: {message}")
    sys.exit(code)


def resolve(env_name, prompt_fn=None, default=None, required=False, label=None):
    """Resolve one value: env -> prompt -> default.

    prompt_fn is a zero-arg callable performing the interactive prompt; it is
    only invoked when interactive. `label` names the value in error messages.
    """
    v = env_str(env_name)
    if v is not None:
        return v
    if not non_interactive() and prompt_fn is not None:
        return prompt_fn()
    if default is not None:
        return default
    if required:
        die(f"{label or env_name} is required. Set {env_name} or run interactively.")
    return None
