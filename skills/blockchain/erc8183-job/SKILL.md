# ERC-8183 Agentic Commerce Job Skill

## When to use this skill

Use this skill when the user asks Hermes to:
- Create a job on Arc Testnet using ERC-8183
- Fund a job with USDC escrow
- Submit a deliverable for a job
- Complete or evaluate a job
- Check the status of an existing job
- Run the full ERC-8183 job lifecycle autonomously

## What this skill does

This skill teaches Hermes to interact with the ERC-8183 AgenticCommerce contract on Arc Testnet.
ERC-8183 is an onchain job lifecycle standard where:
- A client creates a job and funds USDC into escrow
- A provider completes the work and submits a deliverable hash
- An evaluator (often the client) approves the deliverable and releases payment

## Procedure

1. Use the  tool to interact with the AgenticCommerce contract
2. For a full lifecycle: createJob -> setBudget -> approve -> fund -> submit -> complete
3. Each step requires a wallet with sufficient USDC on Arc Testnet (Chain ID: 5042002)
4. Always verify the job status after each step

## Technical notes

- AgenticCommerce contract: 0x0747EEf0706327138c69792bF28Cd525089e4583
- USDC contract (Arc Testnet): 0x3600000000000000000000000000000000000000
- Chain ID: 5042002
- RPC URL: https://rpc.testnet.arc.network
- Explorer: https://testnet.arcscan.app
- Client and provider cannot be the same address
- Budget is set in USDC (6 decimals for ERC-20)

## Job Status Values

- 0: Open
- 1: Funded
- 2: Submitted
- 3: Completed
- 4: Rejected
- 5: Expired

## Safety limits

- Maximum job budget: 0.00 USDC
- Always confirm before funding escrow
- Verify job status before each state transition
- Never expose private keys

## Verified on Arc Testnet

- Job ID: 110935
- Status: Completed
- Budget: 1 USDC
- Client: 0xB4e7E06c93252272F41424450E8b162E2fD5bE72
- Provider: 0x87620233ad9516ae30Dae514c8d199898AE80e01
- Explorer: https://testnet.arcscan.app/address/0xB4e7E06c93252272F41424450E8b162E2fD5bE72

## Example prompts

- "Create a job on Arc Testnet for 1 USDC with provider 0x..."
- "Fund job 110935 with 1 USDC"
- "Submit deliverable for job 110935"
- "Complete job 110935"
- "Check the status of job 110935"
- "Run the full ERC-8183 lifecycle on Arc Testnet"

## Reference implementation

https://github.com/consumeobeydie/arc-agent-api

## Related skills

- skills/blockchain/x402-payment — for x402 micropayments on Arc Testnet
- skills/blockchain/erc8004-identity — for onchain agent identity (coming soon)

## Resources

- Arc ERC-8183 docs: https://docs.arc.network/arc/tutorials/create-your-first-erc-8183-job
- Arc Testnet Explorer: https://testnet.arcscan.app
- ERC-8183 Standard: https://eips.ethereum.org/EIPS/eip-8183
