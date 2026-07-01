#!/usr/bin/env python3
"""
ERC-8183 Agentic Commerce Job Tool for Hermes Agent
Interact with the AgenticCommerce contract on Arc Testnet
"""

import os
import json
import time
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.hermes/.env"))

# Arc Testnet config
ARC_RPC_URL = os.getenv("ARC_RPC_URL", "https://rpc.testnet.arc.network")
ARC_CHAIN_ID = int(os.getenv("ARC_CHAIN_ID", "5042002"))
PRIVATE_KEY = os.getenv("X402_PRIVATE_KEY", "")

# Contract addresses
AGENTIC_COMMERCE = "0x0747EEf0706327138c69792bF28Cd525089e4583"
USDC_CONTRACT = "0x3600000000000000000000000000000000000000"

# Max budget safety limit
MAX_BUDGET_USDC = 10.0

AGENTIC_COMMERCE_ABI = [
    {"name": "createJob", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "provider", "type": "address"}, {"name": "evaluator", "type": "address"},
                {"name": "expiredAt", "type": "uint256"}, {"name": "description", "type": "string"},
                {"name": "hook", "type": "address"}], "outputs": [{"name": "jobId", "type": "uint256"}]},
    {"name": "setBudget", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "jobId", "type": "uint256"}, {"name": "amount", "type": "uint256"},
                {"name": "optParams", "type": "bytes"}], "outputs": []},
    {"name": "fund", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "jobId", "type": "uint256"}, {"name": "optParams", "type": "bytes"}], "outputs": []},
    {"name": "submit", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "jobId", "type": "uint256"}, {"name": "deliverable", "type": "bytes32"},
                {"name": "optParams", "type": "bytes"}], "outputs": []},
    {"name": "complete", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "jobId", "type": "uint256"}, {"name": "reason", "type": "bytes32"},
                {"name": "optParams", "type": "bytes"}], "outputs": []},
    {"name": "getJob", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "jobId", "type": "uint256"}],
     "outputs": [{"type": "tuple", "components": [
         {"name": "id", "type": "uint256"}, {"name": "client", "type": "address"},
         {"name": "provider", "type": "address"}, {"name": "evaluator", "type": "address"},
         {"name": "description", "type": "string"}, {"name": "budget", "type": "uint256"},
         {"name": "expiredAt", "type": "uint256"}, {"name": "status", "type": "uint8"},
         {"name": "hook", "type": "address"}]}]},
]

ERC20_ABI = [
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
]

STATUS_NAMES = ["Open", "Funded", "Submitted", "Completed", "Rejected", "Expired"]


def get_web3():
    w3 = Web3(Web3.HTTPProvider(ARC_RPC_URL))
    if not w3.is_connected():
        raise Exception(f"Cannot connect to Arc Testnet at {ARC_RPC_URL}")
    return w3


def get_account():
    if not PRIVATE_KEY:
        raise Exception("X402_PRIVATE_KEY not set in ~/.hermes/.env")
    pk = PRIVATE_KEY if PRIVATE_KEY.startswith("0x") else "0x" + PRIVATE_KEY
    return Account.from_key(pk)


def send_transaction(w3, account, tx):
    tx["nonce"] = w3.eth.get_transaction_count(account.address)
    tx["chainId"] = ARC_CHAIN_ID
    tx["gas"] = w3.eth.estimate_gas(tx)
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    return tx_hash.hex(), receipt


def create_job(provider: str, description: str, budget_usdc: float, duration_hours: int = 1) -> dict:
    """Create a new ERC-8183 job on Arc Testnet."""
    if budget_usdc > MAX_BUDGET_USDC:
        return {"error": f"Budget exceeds maximum limit of  USDC"}

    w3 = get_web3()
    account = get_account()
    contract = w3.eth.contract(address=Web3.to_checksum_address(AGENTIC_COMMERCE), abi=AGENTIC_COMMERCE_ABI)

    expired_at = int(time.time()) + (duration_hours * 3600)
    tx = contract.functions.createJob(
        Web3.to_checksum_address(provider),
        account.address,
        expired_at,
        description,
        "0x0000000000000000000000000000000000000000"
    ).build_transaction({"from": account.address})

    tx_hash, receipt = send_transaction(w3, account, tx)

    job_id = None
    for log in receipt.logs:
        try:
            decoded = contract.events.JobCreated().process_log(log)
            job_id = decoded["args"]["jobId"]
            break
        except Exception:
            continue

    return {
        "success": True,
        "job_id": str(job_id) if job_id else "unknown",
        "tx_hash": tx_hash,
        "explorer": f"https://testnet.arcscan.app/tx/{tx_hash}",
        "client": account.address,
        "provider": provider,
        "description": description,
        "budget_usdc": budget_usdc,
    }


def get_job_status(job_id: int) -> dict:
    """Get the current status of a job."""
    w3 = get_web3()
    contract = w3.eth.contract(address=Web3.to_checksum_address(AGENTIC_COMMERCE), abi=AGENTIC_COMMERCE_ABI)
    job = contract.functions.getJob(job_id).call()
    return {
        "job_id": str(job[0]),
        "client": job[1],
        "provider": job[2],
        "evaluator": job[3],
        "description": job[4],
        "budget_usdc": str(job[5] / 10**6),
        "status": STATUS_NAMES[job[7]] if job[7] < len(STATUS_NAMES) else str(job[7]),
        "explorer": f"https://testnet.arcscan.app/address/{job[1]}",
    }


def fund_job(job_id: int, budget_usdc: float) -> dict:
    """Approve USDC and fund a job escrow."""
    if budget_usdc > MAX_BUDGET_USDC:
        return {"error": f"Budget exceeds maximum limit of  USDC"}

    w3 = get_web3()
    account = get_account()
    amount = int(budget_usdc * 10**6)

    usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_CONTRACT), abi=ERC20_ABI)
    approve_tx = usdc.functions.approve(
        Web3.to_checksum_address(AGENTIC_COMMERCE), amount
    ).build_transaction({"from": account.address})
    approve_hash, _ = send_transaction(w3, account, approve_tx)

    contract = w3.eth.contract(address=Web3.to_checksum_address(AGENTIC_COMMERCE), abi=AGENTIC_COMMERCE_ABI)
    fund_tx = contract.functions.fund(job_id, b"").build_transaction({"from": account.address})
    fund_hash, _ = send_transaction(w3, account, fund_tx)

    return {
        "success": True,
        "job_id": str(job_id),
        "budget_usdc": budget_usdc,
        "approve_tx": approve_hash,
        "fund_tx": fund_hash,
        "explorer": f"https://testnet.arcscan.app/tx/{fund_hash}",
    }


def complete_job(job_id: int, reason: str = "approved") -> dict:
    """Complete a job as evaluator, releasing payment to provider."""
    w3 = get_web3()
    account = get_account()
    contract = w3.eth.contract(address=Web3.to_checksum_address(AGENTIC_COMMERCE), abi=AGENTIC_COMMERCE_ABI)

    reason_hash = w3.keccak(text=reason)
    tx = contract.functions.complete(job_id, reason_hash, b"").build_transaction({"from": account.address})
    tx_hash, _ = send_transaction(w3, account, tx)

    return {
        "success": True,
        "job_id": str(job_id),
        "tx_hash": tx_hash,
        "explorer": f"https://testnet.arcscan.app/tx/{tx_hash}",
        "status": "Completed",
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: erc8183_job.py <status|create|fund|complete> [args...]")
        sys.exit(1)

    command = sys.argv[1]
    if command == "status" and len(sys.argv) >= 3:
        print(json.dumps(get_job_status(int(sys.argv[2])), indent=2))
    else:
        print("Command not recognized")
