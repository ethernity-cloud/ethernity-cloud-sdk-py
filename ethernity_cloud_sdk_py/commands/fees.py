"""The fee per gas of a transaction on an EIP-1559 network, and the ceiling
above which none is built.

maxFeePerGas is the latest block's base fee plus a tenth, plus the network's
priority fee (max_priority_fee_per_gas in commands/enums.py). Its ceiling is
the environment variable ECLD_MAX_FEE_PER_GAS, a positive whole number of
gwei in up to 30 digits, when that is set and not empty, else the network's
max_fee_per_gas. The image registry's transactions
(pynithy/run/image_registry.py), the SessionRegistry's
(pynithy/session_registry.py) and the check ecld-publish makes beside the gas
balance (commands/publish.py) take their fee here. A transaction of a network
that sends legacy transactions carries the network's gas price, and neither
this module nor the variable is read for it.
"""
import os
import re

from web3 import Web3

MAX_FEE_VARIABLE = "ECLD_MAX_FEE_PER_GAS"


class FeeCeilingError(Exception):
    """A fee per gas above its ceiling, or a MAX_FEE_VARIABLE that is not a
    positive whole number of gwei as max_fee_ceiling reads one; str() is the
    message for the user."""


def max_fee_ceiling(network):
    """The ceiling of maxFeePerGas on `network` (a BlockchainNetworks member)
    in wei, and whether MAX_FEE_VARIABLE set it. The variable, when it is set
    and not empty, is the ceiling in gwei: ASCII decimal digits, at most 30,
    naming a number above zero, any other value (one with a sign, a fraction,
    a unit, surrounding whitespace or more than 30 digits included) raising
    FeeCeilingError with the variable and the value. Without it the ceiling
    is the network's max_fee_per_gas."""
    value = os.environ.get(MAX_FEE_VARIABLE, "")
    if not value:
        return Web3.to_wei(network.max_fee_per_gas, "gwei"), False
    ceiling_gwei = int(value) if re.fullmatch(r"[0-9]{1,30}", value) else 0
    if not ceiling_gwei:
        raise FeeCeilingError(
            f"{MAX_FEE_VARIABLE} is {value!r}, not a positive whole number of gwei in up to 30 digits alone "
            f"(the ceiling of the fee per gas); unset it for the network's default")
    return ceiling_gwei * 10**9, True


def _gwei(wei):
    """`wei` in gwei as exact decimal text, without trailing zeros."""
    whole, fraction = divmod(wei, 10**9)
    return f"{whole}.{fraction:09d}".rstrip("0").rstrip(".")


def eip1559_fee_fields(w3, network):
    """The fee fields of a type-2 transaction on `network` (a
    BlockchainNetworks member) at the latest block `w3` reads: maxFeePerGas
    is that block's base fee plus a tenth, plus the network's priority fee.
    A maxFeePerGas above max_fee_ceiling raises FeeCeilingError with the base
    fee, the fee, the ceiling and what set the ceiling. The ceiling is
    decided before the block is read."""
    ceiling, from_variable = max_fee_ceiling(network)
    base_fee = w3.eth.get_block("latest").baseFeePerGas
    priority = Web3.to_wei(network.max_priority_fee_per_gas, "gwei")
    max_fee = int(base_fee * 1.1) + priority
    if max_fee > ceiling:
        if from_variable:
            limit = f"the ceiling of {_gwei(ceiling)} gwei that {MAX_FEE_VARIABLE} sets"
        else:
            limit = (f"the network's default ceiling of {_gwei(ceiling)} gwei "
                     f"({MAX_FEE_VARIABLE} sets another, in whole gwei)")
        raise FeeCeilingError(
            f"{network.display_name}: a base fee of {_gwei(base_fee)} gwei puts the fee per gas "
            f"at {_gwei(max_fee)} gwei, above {limit}")
    return {"type": 2, "maxFeePerGas": max_fee, "maxPriorityFeePerGas": priority}
