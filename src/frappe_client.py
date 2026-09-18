from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from src.schemas import PipelineResponse

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
        base_url: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        mock_mode: bool | None = None,
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
        self._mock_call_logs: dict[str, dict] = {}
        self._mock_tasks: dict[str, dict] = {}
        self._call_log_counter = 1
        self._task_counter = 1

    def _get_headers(self) -> dict[str, str]:
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

    async def lookup_lead_by_phone(self, phone: str) -> dict[str, Any] | None:
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
                params: dict[str, str | int | float | bool | None] = {
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
                        lookup_suffix in mob_digits
                        or mob_digits.endswith(lookup_suffix)
                        or lookup_suffix in ph_digits
                        or ph_digits.endswith(lookup_suffix)
                    ):
                        logger.info(f"[FrappeCRMClient] Matched Lead '{lead.get('name')}' for phone '{phone}'")
                        return lead

                logger.info(f"[FrappeCRMClient] No existing CRM Lead matched phone '{phone}'.")
                return None
        except Exception as exc:
            logger.warning(f"[FrappeCRMClient] Error querying CRM Lead: {exc}")
            return None

    async def get_call_log_by_provider_id(self, provider_call_id: str) -> dict[str, Any] | None:
        """Check if a Call Log with this provider ID already exists in Frappe CRM (Idempotency)."""
        if self.mock_mode:
            for log in self._mock_call_logs.values():
                if log.get("id") == provider_call_id:
                    return log
            return None

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                url = f"{self.base_url}/api/resource/CRM Call Log"
                params: dict[str, str | int | float | bool | None] = {
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
        lead_id: str | None = None,
    ) -> dict[str, Any]:
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
            try:
                response = await client.post(url, headers=self._get_headers(), json=log_payload)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.error(f"[FrappeCRMClient] CRM Call Log creation failed: {exc.response.text}")
                raise ValueError(f"Frappe Validation Error: {exc.response.text}") from exc

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
        transcript: str | None = None,
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
            f"<li><b>Primary Objection:</b> {intelligence.primary_objection.value if hasattr(intelligence, 'primary_objection') else 'None'}</li>"
            f"<li><b>Objections:</b> {', '.join(intelligence.objections) if intelligence.objections else 'None'}</li>"
            f"<li><b>Next Action:</b> {intelligence.next_action}</li>"
            f"<li><b>Recommended Action:</b> {intelligence.recommended_action or 'None'}</li>"
            f"<li><b>Follow-up Required:</b> {intelligence.follow_up_required}</li>"
            f"<li><b>Follow-up At:</b> {intelligence.follow_up_at.isoformat() if intelligence.follow_up_at else 'None'}</li>"
            f"<li><b>Follow-up Date:</b> {intelligence.follow_up_date.isoformat() if intelligence.follow_up_date else 'None'}</li>"
            f"<li><b>Follow-up Notes:</b> {intelligence.follow_up_notes or 'None'}</li>"
            f"<li><b>Key Points:</b> {', '.join(intelligence.key_points) if intelligence.key_points else 'None'}</li>"
            f"<li><b>Agent Quality Notes:</b> {intelligence.agent_quality_notes}</li>"
            f"</ul>"
        )
        privacy_mode = get_settings().store_transcript_in_crm
        if transcript and privacy_mode != "none":
            if privacy_mode == "truncated" and len(transcript) > 500:
                displayed_transcript = transcript[:500] + "\n... [Transcript truncated for privacy]"
                label = "View Audio Transcript (Truncated)"
            else:
                displayed_transcript = transcript
                label = "View Audio Transcript"

            html_content += (
                f"<details><summary><b>{label}</b></summary>"
                f"<pre style='white-space: pre-wrap; font-size: 11px; background: #f8f9fa; padding: 8px; border-radius: 4px;'>"
                f"{displayed_transcript}"
                f"</pre></details>"
            )
        html_content += "</div>"

        comment_payload = {
            "doctype": "Comment",
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "content": html_content,
            "comment_type": "Comment",
            "comment_by": "AI Automation Pipeline",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f"{self.base_url}/api/resource/Comment"
            try:
                res = await client.post(url, headers=self._get_headers(), json=comment_payload)
                res.raise_for_status()
                return True
            except httpx.HTTPStatusError as exc:
                logger.error(
                    f"[FrappeCRMClient] Failed to add timeline comment to {reference_doctype} '{reference_name}': "
                    f"HTTP {exc.response.status_code}"
                )
                return False
            except Exception as exc:
                logger.error(
                    f"[FrappeCRMClient] Network/connection error adding timeline comment to {reference_doctype} '{reference_name}': {exc}"
                )
                return False

    async def create_followup_task(
        self,
        call_log_id: str,
        lead_id: str | None,
        intelligence: CallIntelligence,
        assigned_to: str | None = None,
    ) -> dict[str, Any] | None:
        """Create a Follow-up CRM Task in Frappe CRM if requested."""
        if not intelligence.follow_up_required:
            return None

        # Priority mapping
        priority = "High" if intelligence.lead_quality == LeadQuality.HOT else "Medium"

        follow_up_date_val = getattr(intelligence, "follow_up_date", None)
        follow_up_at_val = getattr(intelligence, "follow_up_at", None)

        if follow_up_date_val is not None:
            due_date_str = follow_up_date_val.strftime("%Y-%m-%d %H:%M:%S")
        elif follow_up_at_val is not None:
            due_date_str = follow_up_at_val.strftime("%Y-%m-%d %H:%M:%S")
        else:
            fallback_dt = datetime.now() + timedelta(days=2)
            due_date_str = fallback_dt.strftime("%Y-%m-%d %H:%M:%S")

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
            "title": f"Follow-up: {intelligence.recommended_action[:60] if intelligence.recommended_action else intelligence.next_action[:60]}",
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
                f"<b>Next Action Required:</b> {intelligence.recommended_action or intelligence.next_action}<br/>"
                f"<b>Follow-up Notes:</b> {intelligence.follow_up_notes or 'None'}<br/>"
                f"<b>Objections:</b> {', '.join(intelligence.objections) if intelligence.objections else (intelligence.primary_objection.value if hasattr(intelligence, 'primary_objection') else 'None')}<br/>"
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

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                url = f"{self.base_url}/api/resource/CRM Task"
                response = await client.post(url, headers=self._get_headers(), json=task_payload)
                response.raise_for_status()
                created_task = response.json().get("data", {})
                logger.info(f"[FrappeCRMClient] Successfully created live CRM Task: {created_task.get('name')}")
                return created_task
        except Exception as exc:
            logger.warning(
                f"[FrappeCRMClient] Follow-up Task creation failed non-fatally for call log {call_log_id}: {exc}"
            )
            return None

    async def update_lead_status(
        self,
        lead_id: str,
        intelligence: CallIntelligence,
    ) -> dict[str, Any]:
        """Update lead status in Frappe CRM and post timeline summary."""
        updates: dict[str, Any] = {}

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

    async def get_recent_call_logs(self, limit: int = 50) -> list[PipelineResponse]:
        """Fetch recent call logs from Frappe CRM and map to PipelineResponse for dashboard."""
        from src.schemas import PipelineResponse

        if self.mock_mode:
            return []

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                url = f"{self.base_url}/api/resource/CRM Call Log"
                params: dict[str, str | int | float | bool | None] = {
                    "fields": '["name","id","duration","status","start_time","receiver","caller","owner","reference_docname","from","to"]',
                    "limit_page_length": limit,
                    "order_by": "start_time desc",
                }
                response = await client.get(url, headers=self._get_headers(), params=params)
                response.raise_for_status()
                data = response.json().get("data", [])

                results = []
                for row in data:
                    dt = None
                    if row.get("start_time"):
                        try:
                            # Frappe returns format YYYY-MM-DD HH:MM:SS
                            dt = datetime.strptime(row["start_time"].split(".")[0], "%Y-%m-%d %H:%M:%S")
                        except Exception:
                            dt = datetime.now()


                    intel = await self._fetch_and_parse_intelligence(row.get("name"), client)

                    resp = PipelineResponse(
                        success=(row.get("status") == "Completed"),
                        provider_call_id=row.get("id") or row.get("name"),
                        idempotent_replay=False,
                        frappe_call_log_id=row.get("name"),
                        duration_seconds=int(row.get("duration") or 0),
                        event_timestamp=dt,
                        agent_id=row.get("owner") or row.get("caller") or row.get("receiver"),
                        matched_lead=row.get("reference_docname"),
                        intelligence=intel,
                        message="Fetched from Live Frappe CRM"
                    )
                    results.append(resp)
                return results
        except Exception as exc:
            logger.error(f"[FrappeCRMClient] Error fetching recent call logs: {exc}")
            return []

    async def _fetch_and_parse_intelligence(self, call_log_name: str, client: httpx.AsyncClient) -> CallIntelligence | None:
        """Fetch Timeline Comments for a Call Log and parse out the structured CallIntelligence data."""
        if not call_log_name:
            return None

        try:
            url = f"{self.base_url}/api/resource/Comment"
            params: dict[str, str | int | float | bool | None] = {
                "filters": f'[["reference_name","=","{call_log_name}"],["reference_doctype","=","CRM Call Log"]]',
                "fields": '["content"]',
                "limit_page_length": 5,
                "order_by": "creation desc",
            }
            response = await client.get(url, headers=self._get_headers(), params=params)
            if response.status_code != 200:
                return None

            comments = response.json().get("data", [])
            for comment in comments:
                content = comment.get("content", "")
                if "AI Call Intelligence Analysis" in content:
                    return self._parse_html_comment(content)
            return None
        except Exception as exc:
            logger.warning(f"[FrappeCRMClient] Failed to fetch intelligence for Call Log {call_log_name}: {exc}")
            return None

    def _parse_html_comment(self, html: str) -> CallIntelligence:
        """Parse the HTML comment string back into a CallIntelligence model."""
        import re

        from src.schemas import CallOutcome, LeadQuality, PrimaryObjection

        def extract(label: str) -> str:
            match = re.search(rf"<li><b>{label}:</b>\s*(.*?)(?:</li>|<br>)", html, re.IGNORECASE | re.DOTALL)
            return match.group(1).strip() if match else ""

        def extract_summary() -> str:
            match = re.search(r"<b>Summary:</b>\s*(.*?)(?:</p>|<br>)", html, re.IGNORECASE | re.DOTALL)
            return match.group(1).strip() if match else ""

        outcome_str = extract("Outcome")
        lead_quality_str = extract("Lead Quality")
        intent_str = extract("Customer Intent")
        objection_str = extract("Primary Objection")
        action_str = extract("Next Action")
        follow_up_str = extract("Follow-up At")
        notes_str = extract("Agent Quality Notes")
        summary_str = extract_summary()

        # Phase 3 Fields
        objections_list_str = extract("Objections")
        rec_action_str = extract("Recommended Action")
        follow_up_req_str = extract("Follow-up Required")
        follow_up_date_str = extract("Follow-up Date")
        follow_up_notes_str = extract("Follow-up Notes")
        key_points_str = extract("Key Points")

        # Parse Enum fields
        try:
            outcome = CallOutcome(outcome_str)
        except ValueError:
            outcome = CallOutcome.OTHER

        try:
            quality = LeadQuality(lead_quality_str)
        except ValueError:
            quality = LeadQuality.UNKNOWN

        try:
            objection = PrimaryObjection(objection_str)
        except ValueError:
            objection = PrimaryObjection.NONE

        # Parse follow up date
        dt = None
        if follow_up_str and follow_up_str.lower() != "none":
            try:
                dt = datetime.fromisoformat(follow_up_str)
            except ValueError:
                pass

        dt_new = None
        if follow_up_date_str and follow_up_date_str.lower() != "none":
            try:
                dt_new = datetime.fromisoformat(follow_up_date_str)
            except ValueError:
                pass

        return CallIntelligence(
            call_summary=summary_str or "Summary extracted from timeline.",
            call_outcome=outcome,
            lead_quality=quality,
            primary_objection=objection,
            customer_intent=intent_str,
            next_action=action_str,
            follow_up_at=dt,
            agent_quality_notes=notes_str,

            # Phase 3
            key_points=[k.strip() for k in key_points_str.split(",")] if key_points_str and key_points_str != "None" else [],
            follow_up_required=(follow_up_req_str.lower() == "true"),
            follow_up_date=dt_new,
            follow_up_notes=follow_up_notes_str if follow_up_notes_str != "None" else None,
            objections=[o.strip() for o in objections_list_str.split(",")] if objections_list_str and objections_list_str != "None" else [],
            recommended_action=rec_action_str if rec_action_str != "None" else None
        )



def get_frappe_client(settings: Settings | None = None) -> FrappeCRMClient:
    """Factory to retrieve Frappe CRM Client instance."""
    cfg = settings or get_settings()
    return FrappeCRMClient(
        base_url=cfg.frappe_base_url,
        api_key=cfg.frappe_api_key,
        api_secret=cfg.frappe_api_secret,
        mock_mode=cfg.mock_mode,
    )
