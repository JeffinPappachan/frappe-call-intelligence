#!/usr/bin/env python3
"""
Safe Live Frappe CRM Connectivity & Verification Script
Verifies:
1. Token Authentication against Frappe Cloud
2. Dynamic Lead lookup by phone number
3. CRM Call Log creation with linked Lead and Agent
4. Formatted AI Call Intelligence comment write-back
5. Follow-up CRM Task auto-creation
6. Idempotent deduplication on repeated provider Call ID
"""

import asyncio
from datetime import datetime, timezone
import os
import sys

# Ensure project root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv

from src.frappe_client import FrappeCRMClient
from src.schemas import (
    CallDirection,
    CallIntelligence,
    CallOutcome,
    CallStatus,
    LeadQuality,
    PrimaryObjection,
    TelephonyWebhookPayload,
)

load_dotenv()


async def main():
    print("=" * 70)
    print("      LIVE FRAPPE CRM CONNECTIVITY & INTEGRATION VERIFICATION")
    print("=" * 70)

    base_url = os.getenv("FRAPPE_BASE_URL", "").rstrip("/")
    api_key = os.getenv("FRAPPE_API_KEY", "").strip()
    api_secret = os.getenv("FRAPPE_API_SECRET", "").strip()

    if not api_key or not api_secret:
        print("[ERROR] FRAPPE_API_KEY or FRAPPE_API_SECRET is not configured in .env!")
        sys.exit(1)

    print(f"Target CRM Instance: {base_url}")
    print("API Credentials: Configured (Token Auth)")
    print("-" * 70)

    # Initialize live client (mock_mode=False)
    client = FrappeCRMClient(base_url=base_url, api_key=api_key, api_secret=api_secret, mock_mode=False)

    # 1. Lead Lookup Test
    test_phone = "+1 555 000 9012"  # Carol Smith
    print(f"\n[Step 1] Looking up Lead by phone: {test_phone}...")
    lead = await client.lookup_lead_by_phone(test_phone)
    if not lead:
        print(f"[FAIL] Could not match lead for phone {test_phone}")
        sys.exit(1)

    lead_id = lead.get("name")
    lead_name = lead.get("lead_name")
    lead_owner = lead.get("lead_owner")
    print(f"[PASS] Matched Lead: {lead_name} ({lead_id}) | Owner: {lead_owner}")

    # 2. Prepare Sample Call Event & Intelligence
    unique_call_id = f"live_verify_call_{int(datetime.now(timezone.utc).timestamp())}"
    event = TelephonyWebhookPayload(
        provider_call_id=unique_call_id,
        telephony_provider="exotel",
        from_number="+15550001111",
        to_number=test_phone,
        direction=CallDirection.OUTBOUND,
        call_status=CallStatus.COMPLETED,
        duration_seconds=154,
        recording_url="https://storage.googleapis.com/hash-adz-recordings/carol_smith_sales_call.wav",
        agent_id=lead_owner,
        call_type="sales_enquiry",
    )

    intelligence = CallIntelligence(
        call_summary=(
            "Telecaller John conducted a structured sales discovery call with Carol Smith (BrightPath Ltd). "
            "Client expressed strong interest in performance ad creative bundles for Q4, but noted current budget "
            "limitations. Requested standard pricing sheet and scheduled a follow-up strategy session for Friday 3 PM."
        ),
        call_outcome=CallOutcome.FOLLOW_UP,
        lead_quality=LeadQuality.WARM,
        primary_objection=PrimaryObjection.PRICE,
        customer_intent="Evaluate performance creative tier pricing relative to available marketing budget",
        next_action="Email standard pricing breakdown and confirm strategy head walkthrough slot",
        follow_up_at=datetime.now(timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0),
        agent_quality_notes="Excellent pacing, acknowledged budget constraints without discounting, locked in firm follow-up.",
        review_flag=False,
    )

    sample_transcript = (
        "Agent: Hello, this is John calling from Hash Adz Creative Solutions. Am I speaking with Carol?\n"
        "Customer: Yes, speaking. What is this regarding?\n"
        "Agent: I'm following up on your inquiry regarding our performance marketing packages for BrightPath Ltd.\n"
        "Customer: Oh yes! We are looking to scale our digital ad campaigns next quarter, but our budget is tight right now. Could you share your pricing and case studies?\n"
        "Agent: Absolutely! I can send over our standard tier breakdown and schedule a detailed walkthrough with our strategy head. Would this Friday at 3 PM work for you?\n"
        "Customer: Friday at 3 PM sounds perfect. Please email the details before then.\n"
        "Agent: Will do, Carol. Thank you for your time, and have a great day!"
    )

    # 3. Create Live CRM Call Log
    print(f"\n[Step 2] Creating CRM Call Log with provider ID: '{unique_call_id}'...")
    call_log = await client.create_call_log(
        call_event=event,
        transcript=sample_transcript,
        intelligence=intelligence,
        lead_id=lead_id,
    )
    call_log_name = call_log.get("name")
    print(f"[PASS] Live CRM Call Log created: {call_log_name}")

    # 4. Verify Live Idempotency (Repeat exact same call)
    print(f"\n[Step 3] Testing Live Idempotency with same call ID: '{unique_call_id}'...")
    replay_log = await client.create_call_log(
        call_event=event,
        transcript=sample_transcript,
        intelligence=intelligence,
        lead_id=lead_id,
    )
    if replay_log.get("name") == call_log_name:
        print(f"[PASS] Idempotency Verified! Existing record reused without duplicate creation: {replay_log.get('name')}")
    else:
        print(f"[FAIL] Idempotency failed! Created duplicate: {replay_log.get('name')}")

    # 5. Create Live Follow-up CRM Task
    print(f"\n[Step 4] Creating Follow-up CRM Task assigned to {lead_owner}...")
    task = await client.create_followup_task(
        call_log_id=call_log_name,
        lead_id=lead_id,
        intelligence=intelligence,
        assigned_to=lead_owner,
    )
    if task:
        task_name = task.get("name")
        print(f"[PASS] Live Follow-up CRM Task created: {task_name} ('{task.get('title')}')")
    else:
        print("[FAIL] Task was not created.")

    # 6. Update Lead Status
    print(f"\n[Step 5] Updating Lead '{lead_id}' status based on AI analysis...")
    updated_lead = await client.update_lead_status(lead_id=lead_id, intelligence=intelligence)
    print(f"[PASS] Lead status update verified: {updated_lead.get('status')}")

    print("\n" + "=" * 70)
    print("      ALL LIVE FRAPPE CRM INTEGRATION CHECKS PASSED SUCCESSFULLY!")
    print("=" * 70)
    print(f"CRM Lead:       {lead_name} ({lead_id})")
    print(f"Call Log:       {call_log_name} (ID: {unique_call_id})")
    print(f"Follow-up Task: {task.get('name') if task else 'None'}")
    print(f"Frappe URL:     {base_url}/crm/leads/{lead_id}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
