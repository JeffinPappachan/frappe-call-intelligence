from datetime import datetime, timedelta, timezone
import logging
import re
from typing import Any, Dict, List, Optional
import httpx

from src.config import Settings, get_settings
from src.schemas import (
    CallDirection,
    CallIntelligence,
    CallOutcome,
    CallStatus,
    LeadQuality,
    TelephonyWebhookPayload,
)

logger = logging.getLogger(__name__)


class FrappeCRMClient:
    """REST Client for interacting with Frappe CRM (Frappe Cloud or self-hosted).

    Supports:
    - Secure Token Authentication (Authorization: token api_key:api_secret)
    - Dynamic Lead lookup by normalized phone numbers
    - Live CRM Call Log creation with provider ID deduplication
    - Automated Follow-up CRM Task scheduling
    - Lead stage updates and activity timeline comments
    - Fallback in-memory simulation mode (MOCK_MODE=true)
    """

    # Realistic mock leads mirroring the live Frappe Cloud CRM instance
    MOCK_LEADS = [
        {
            "name": "CRM-LEAD-2026-00003",
            "lead_name": "Carol Smith",
            "organization": "BrightPath Ltd",
            "email": "carol.smith@example.com",
            "mobile_no": "+1 555 000 9012",
            "lead_owner": "john.demo@example.com",
            "status": "Nurture",
        },
        {
            "name": "CRM-LEAD-2026-00002",
            "lead_name": "Bob Martinez",
            "organization": "Globex Industries",
            "email": "bob.martinez@example.com",
            "mobile_no": "+1 555 000 5678",
            "lead_owner": "sarah.demo@example.com",
            "status": "Contacted",
        },
        {
            "name": "CRM-LEAD-2026-00001",
            "lead_name": "Alice Johnson",
            "organization": "Acme Corp",
            "email": "alice.johnson@example.com",
            "mobile_no": "+1 555 000 1234",
            "lead_owner": "sarah.demo@example.com",
            "status": "Qualified",
        },
        {
            "name": "CRM-LEAD-2026-00004",
            "lead_name": "David Lee",
            "organization": "NextWave Corp",
            "email": "david.lee@example.com",
            "mobile_no": "+1 555 000 3456",
            "lead_owner": "sarah.demo@example.com",
            "status": "Qualified",
        },
        {
            "name": "CRM-LEAD-2026-00005",
            "lead_name": "Emma Williams",
            "organization": "CloudBase LLC",
            "email": "emma.williams@example.com",
            "mobile_no": "+1 555 000 2345",
            "lead_owner": "sarah.demo@example.com",
            "status": "New",
        },
    ]

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        mock_mode: Optional[bool] = None,
    ):
        settings = get_settings()
        self.base_url = (base_url or settings.frappe_base_url).rstrip("/")
        self.api_key = api_key or settings.frappe_api_key
        self.api_secret = api_secret or settings.frappe_api_secret

        if mock_mode is not None:
            self.mock_mode = mock_mode
        else:
            # If explicit keys are configured and mock_mode isn't forced True, run live
            self.mock_mode = settings.mock_mode or not (self.api_key and self.api_secret)

        # In-memory stores for simulation mode
        self._mock_call_logs: Dict[str, dict] = {}
        self._mock_tasks: Dict[str, dict] = {}
        self._call_log_counter = 1
        self._task_counter = 1

    def _get_headers(self) -> Dict[str, str]:
        """Generate Frappe Token Authentication headers without exposing secrets."""
        return {
            "Authorization": f"token {self.api_key}:{self.api_secret}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _normalize_phone_digits(phone: str) -> str:
        """Extract only numerical digits from a phone string."""
        return re.sub(r"\D", "", phone or "")

    async def lookup_lead_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """Find a CRM Lead by matching against phone or mobile number."""
        clean_target = self._normalize_phone_digits(phone)
        if not clean_target:
            return None

        # Compare using last 7 to 10 digits to handle country code variations
        lookup_suffix = clean_target[-10:] if len(clean_target) >= 10 else clean_target

        if self.mock_mode:
            logger.info(f"[FrappeCRMClient] Mock lookup for phone: {phone}")
            for lead in self.MOCK_LEADS:
                lead_digits = self._normalize_phone_digits(lead.get("mobile_no", ""))
                if lookup_suffix in lead_digits or lead_digits.endswith(lookup_suffix):
                    return lead
            return self.MOCK_LEADS[0]

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                url = f"{self.base_url}/api/resource/CRM Lead"
                params = {
                    "fields": '["name","lead_name","email","mobile_no","phone","lead_owner","status"]',
                    "limit_page_length": 50,
                }
                response = await client.get(url, headers=self._get_headers(), params=params)
                response.raise_for_status()
                leads = response.json().get("data", [])

                for lead in leads:
                    mob_digits = self._normalize_phone_digits(lead.get("mobile_no", ""))
                    ph_digits = self._normalize_phone_digits(lead.get("phone", ""))
                    if (
                        (lookup_suffix and (lookup_suffix in mob_digits or mob_digits.endswith(lookup_suffix)))
                        or (lookup_suffix and (lookup_suffix in ph_digits or ph_digits.endswith(lookup_suffix)))
                    ):
                        logger.info(f"[FrappeCRMClient] Matched Lead '{lead.get('lead_name')}' ({lead.get('name')})")
                        return lead

                logger.warning(f"[FrappeCRMClient] No CRM Lead matched for phone {phone}")
                return None
        except httpx.HTTPStatusError as exc:
            logger.error(f"[FrappeCRMClient] Lead lookup HTTP error: {exc.response.status_code}")
            return None
        except Exception as exc:
            logger.error(f"[FrappeCRMClient] Lead lookup connection error: {exc}")
            return None

    async def get_call_log_by_provider_id(self, provider_call_id: str) -> Optional[Dict[str, Any]]:
        """Check if a Call Log with this provider ID already exists in Frappe CRM (Idempotency)."""
        if self.mock_mode:
            for log in self._mock_call_logs.values():
                if log.get("id") == provider_call_id:
                    return log
            return None

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                url = f"{self.base_url}/api/resource/CRM Call Log"
                params = {
                    "filters": f'[["id","=","{provider_call_id}"]]',
                    "fields": '["name","id","from","to","status","reference_docname","duration"]',
                    "limit_page_length": 1,
                }
                response = await client.get(url, headers=self._get_headers(), params=params)
                response.raise_for_status()
                data = response.json().get("data", [])
                return data[0] if data else None
        except Exception as exc:
            logger.warning(f"[FrappeCRMClient] Error checking existing call log: {exc}")
            return None

    def _map_telephony_medium(self, provider: str) -> str:
        prov = (provider or "").lower()
        if "exotel" in prov:
            return "Exotel"
        if "twilio" in prov:
            return "Twilio"
        return "Manual"

    def _map_call_type(self, direction: CallDirection) -> str:
        return "Incoming" if direction == CallDirection.INBOUND else "Outgoing"

    def _map_call_status(self, status: CallStatus) -> str:
        status_map = {
            CallStatus.COMPLETED: "Completed",
            CallStatus.NO_ANSWER: "No Answer",
            CallStatus.BUSY: "Busy",
            CallStatus.FAILED: "Failed",
            CallStatus.CANCELED: "Canceled",
        }
        return status_map.get(status, "Completed")

    async def create_call_log(
        self,
        call_event: TelephonyWebhookPayload,
        transcript: str,
        intelligence: CallIntelligence,
        lead_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a Call Log in Frappe CRM and attach structured intelligence as a timeline comment."""
        # 1. Idempotency check against live Frappe CRM
        existing = await self.get_call_log_by_provider_id(call_event.provider_call_id)
        if existing:
            logger.info(f"[FrappeCRMClient] CRM Call Log already exists: {existing.get('name')}")
            return existing

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        start_time_str = (
            (datetime.now() - timedelta(seconds=call_event.duration_seconds)).strftime("%Y-%m-%d %H:%M:%S")
            if call_event.duration_seconds
            else now_str
        )

        # Resolve receiver safely for Frappe User link field
        receiver_user = None
        if call_event.agent_id:
            agent_lower = call_event.agent_id.lower()
            if "john" in agent_lower:
                receiver_user = "john.demo@example.com"
            elif "sarah" in agent_lower:
                receiver_user = "sarah.demo@example.com"
            elif "emily" in agent_lower:
                receiver_user = "emily.demo@example.com"
            elif "jeffin" in agent_lower:
                receiver_user = "jeffinpappachan110@gmail.com"
            elif "@" in call_event.agent_id:
                receiver_user = call_event.agent_id

        log_payload = {
            "doctype": "CRM Call Log",
            "telephony_medium": self._map_telephony_medium(call_event.telephony_provider),
            "id": call_event.provider_call_id,
            "from": call_event.from_number,
            "to": call_event.to_number,
            "type": self._map_call_type(call_event.direction),
            "status": self._map_call_status(call_event.call_status),
            "duration": float(call_event.duration_seconds),
            "start_time": start_time_str,
            "end_time": now_str,
            "recording_url": call_event.recording_url or "",
            "reference_doctype": "CRM Lead" if lead_id else None,
            "reference_docname": lead_id if lead_id else None,
            "receiver": receiver_user or None,
        }

        if self.mock_mode:
            log_id = f"CALL-LOG-{self._call_log_counter:05d}"
            self._call_log_counter += 1
            log_payload["name"] = log_id
            self._mock_call_logs[log_id] = log_payload
            logger.info(f"[FrappeCRMClient] Created mock Call Log: {log_id}")
            return log_payload

        async with httpx.AsyncClient(timeout=15.0) as client:
            url = f"{self.base_url}/api/resource/CRM Call Log"
            response = await client.post(url, headers=self._get_headers(), json=log_payload)
            response.raise_for_status()
            created_log = response.json().get("data", {})
            created_log_name = created_log.get("name")
            logger.info(f"[FrappeCRMClient] Successfully created live CRM Call Log: {created_log_name}")

            # Attach structured AI analysis as a timeline Comment
            try:
                await self.add_timeline_comment(
                    reference_doctype="CRM Call Log",
                    reference_name=created_log_name,
                    title="AI Call Intelligence Analysis",
                    intelligence=intelligence,
                    transcript=transcript,
                )
            except Exception as exc:
                logger.warning(f"[FrappeCRMClient] Non-fatal: could not add comment to Call Log: {exc}")

            return created_log

    async def add_timeline_comment(
        self,
        reference_doctype: str,
        reference_name: str,
        title: str,
        intelligence: CallIntelligence,
        transcript: Optional[str] = None,
    ) -> bool:
        """Post a rich HTML comment to the document's timeline in Frappe Desk."""
        if self.mock_mode:
            return True

        review_badge = (
            '<span style="color:red; font-weight:bold;">[NEEDS MANAGER REVIEW]</span>'
            if intelligence.review_flag
            else '<span style="color:green;">[Verified]</span>'
        )

        html_content = (
            f"<div>"
            f"<h4>{title} {review_badge}</h4>"
            f"<p><b>Summary:</b> {intelligence.call_summary}</p>"
            f"<ul>"
            f"<li><b>Outcome:</b> {intelligence.call_outcome.value}</li>"
            f"<li><b>Lead Quality:</b> {intelligence.lead_quality.value}</li>"
            f"<li><b>Customer Intent:</b> {intelligence.customer_intent}</li>"
            f"<li><b>Primary Objection:</b> {intelligence.primary_objection.value}</li>"
            f"<li><b>Next Action:</b> {intelligence.next_action}</li>"
            f"<li><b>Follow-up At:</b> {intelligence.follow_up_at.isoformat() if intelligence.follow_up_at else 'None'}</li>"
            f"<li><b>Agent Quality Notes:</b> {intelligence.agent_quality_notes}</li>"
            f"</ul>"
        )
        if transcript:
            html_content += (
                f"<details><summary><b>View Audio Transcript</b></summary>"
                f"<pre style='white-space: pre-wrap; font-size: 11px; background: #f8f9fa; padding: 8px; border-radius: 4px;'>"
                f"{transcript}"
                f"</pre></details>"
            )
        html_content += "</div>"

        comment_payload = {
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "content": html_content,
            "comment_by": "AI Automation Pipeline",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f"{self.base_url}/api/method/frappe.desk.form.utils.add_comment"
            res = await client.post(url, headers=self._get_headers(), json=comment_payload)
            return res.status_code == 200

    async def create_followup_task(
        self,
        call_log_id: str,
        lead_id: Optional[str],
        intelligence: CallIntelligence,
        assigned_to: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create a Follow-up CRM Task in Frappe CRM if requested."""
        if not intelligence.follow_up_at and intelligence.call_outcome != CallOutcome.FOLLOW_UP:
            return None

        # Priority mapping
        priority = "High" if intelligence.lead_quality == LeadQuality.HOT else "Medium"

        due_date_str = (
            intelligence.follow_up_at.strftime("%Y-%m-%d %H:%M:%S")
            if intelligence.follow_up_at
            else (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        )

        assigned_user = assigned_to or ""
        if assigned_user:
            agent_lower = assigned_user.lower()
            if "john" in agent_lower:
                assigned_user = "john.demo@example.com"
            elif "sarah" in agent_lower:
                assigned_user = "sarah.demo@example.com"
            elif "emily" in agent_lower:
                assigned_user = "emily.demo@example.com"
            elif "jeffin" in agent_lower:
                assigned_user = "jeffinpappachan110@gmail.com"

        task_payload = {
            "doctype": "CRM Task",
            "title": f"Follow-up: {intelligence.next_action[:60]}",
            "priority": priority,
            "status": "Todo",
            "due_date": due_date_str,
            "reference_doctype": "CRM Lead" if lead_id else "CRM Call Log",
            "reference_docname": lead_id or call_log_id,
            "assigned_to": assigned_user or None,
            "description": (
                f"<b>Generated by AI Call Intelligence</b><br/>"
                f"<b>Call Log Reference:</b> {call_log_id}<br/>"
                f"<b>Call Summary:</b> {intelligence.call_summary}<br/>"
                f"<b>Next Action Required:</b> {intelligence.next_action}<br/>"
                f"<b>Primary Objection:</b> {intelligence.primary_objection.value}<br/>"
                f"<b>Quality Notes:</b> {intelligence.agent_quality_notes}"
            ),
        }

        if self.mock_mode:
            task_id = f"TASK-{self._task_counter:05d}"
            self._task_counter += 1
            task_payload["name"] = task_id
            self._mock_tasks[task_id] = task_payload
            logger.info(f"[FrappeCRMClient] Created mock Follow-up Task: {task_id}")
            return task_payload

        async with httpx.AsyncClient(timeout=15.0) as client:
            url = f"{self.base_url}/api/resource/CRM Task"
            response = await client.post(url, headers=self._get_headers(), json=task_payload)
            response.raise_for_status()
            created_task = response.json().get("data", {})
            logger.info(f"[FrappeCRMClient] Successfully created live CRM Task: {created_task.get('name')}")
            return created_task

    async def update_lead_status(
        self,
        lead_id: str,
        intelligence: CallIntelligence,
    ) -> Dict[str, Any]:
        """Update lead status in Frappe CRM and post timeline summary."""
        updates: Dict[str, Any] = {}

        # Map outcome to supported CRM Lead statuses:
        # ['New', 'Contacted', 'Nurture', 'Qualified', 'Converted', 'Unqualified', 'Junk']
        if intelligence.call_outcome == CallOutcome.NOT_INTERESTED:
            updates["status"] = "Unqualified"
        elif intelligence.call_outcome in (CallOutcome.INTERESTED, CallOutcome.FOLLOW_UP):
            if intelligence.lead_quality == LeadQuality.HOT:
                updates["status"] = "Qualified"
            else:
                updates["status"] = "Contacted"

        if self.mock_mode:
            logger.info(f"[FrappeCRMClient] Updated mock Lead {lead_id}: {updates}")
            return {"name": lead_id, **updates}

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                url = f"{self.base_url}/api/resource/CRM Lead/{lead_id}"
                response = await client.put(url, headers=self._get_headers(), json=updates)
                response.raise_for_status()
                updated_lead = response.json().get("data", {})
                logger.info(f"[FrappeCRMClient] Successfully updated Lead '{lead_id}' status to {updates.get('status')}")

                # Also post timeline comment on the Lead
                try:
                    await self.add_timeline_comment(
                        reference_doctype="CRM Lead",
                        reference_name=lead_id,
                        title="AI Call Intelligence Update",
                        intelligence=intelligence,
                    )
                except Exception as exc:
                    logger.warning(f"[FrappeCRMClient] Non-fatal: could not add comment to Lead {lead_id}: {exc}")

                return updated_lead
        except Exception as exc:
            logger.error(f"[FrappeCRMClient] Error updating Lead '{lead_id}': {exc}")
            return {"name": lead_id, "error": str(exc)}


def get_frappe_client(settings: Optional[Settings] = None) -> FrappeCRMClient:
    """Factory to retrieve Frappe CRM Client instance."""
    cfg = settings or get_settings()
    return FrappeCRMClient(
        base_url=cfg.frappe_base_url,
        api_key=cfg.frappe_api_key,
        api_secret=cfg.frappe_api_secret,
        mock_mode=cfg.mock_mode,
    )
