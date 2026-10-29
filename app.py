import streamlit as st
import pandas as pd
import json
import time
import os
import io
import uuid
import hashlib
from datetime import datetime, timedelta, timezone, time as dtime
from typing import Any, Dict, List, Optional, Tuple, Literal
from pydantic import BaseModel, Field, ValidationError
from PIL import Image, ImageDraw, ImageFont

st.set_page_config(
    page_title="Workflow Orchestrator Agent",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)

USER_EMAIL = "me@company.com"

SIDE_EFFECT_TYPES = {
    "send_reply",
    "send_draft_reply",
    "send_team_notification",
    "create_calendar_event",
    "update_calendar_event",
    "flag_for_security_review",
    "draft_reply",
}

BLOCKED_TYPES = {
    "transfer_funds",
    "wire_transfer",
    "payment",
    "purchase_gift_cards",
}

FINANCIAL_KEYWORDS = [
    "transfer",
    "wire",
    "payment",
    "invoice",
    "account",
    "iban",
    "routing",
    "gift card",
    "urgent",
]

EXECUTIVE_KEYWORDS = [
    "ceo",
    "cfo",
    "president",
    "founder",
    "executive",
    "board",
]

SCENARIOS = {
    "All scenarios": ["em_001", "em_002", "em_003", "em_004", "em_005"],
    "Meeting request": ["em_001"],
    "Spoofed CEO": ["em_002"],
    "Email chain": ["em_003", "em_004", "em_005"],
}


class EmailMessage(BaseModel):
    id: str
    thread_id: str
    sender: str
    sender_name: str = ""
    recipient: str = USER_EMAIL
    subject: str
    body: str
    received_at: datetime
    labels: List[str] = Field(default_factory=list)
    external: bool = False
    auth_pass: bool = True
    reply_to: Optional[str] = None


class CalendarEvent(BaseModel):
    id: str
    title: str
    start: datetime
    end: datetime
    attendees: List[str] = Field(default_factory=list)
    timezone_offset_hours: float = -5.0
    status: str = "confirmed"
    source: str = "calendar"


class TaskItem(BaseModel):
    id: str
    title: str
    owner: str
    deadline: Optional[datetime] = None
    source_email_id: str
    priority: Literal["low", "medium", "high"] = "medium"


class ProposedAction(BaseModel):
    id: str
    type: str
    payload: Dict[str, Any]
    risk: Literal["low", "medium", "high"]
    needs_approval: bool = True
    status: Literal[
        "pending",
        "approved",
        "rejected",
        "edited",
        "executed",
        "expired",
        "undone",
    ] = "pending"
    reasoning: str = ""
    evidence_ids: List[str] = Field(default_factory=list)
    created_at: datetime
    decided_at: Optional[datetime] = None
    note: str = ""
    undo_expires_at: Optional[datetime] = None


class RunResult(BaseModel):
    email_id: str
    intent: Literal["schedule", "reply", "delegate", "ignore"]
    tasks: List[TaskItem] = Field(default_factory=list)
    proposed_actions: List[ProposedAction] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    reasoning: str


class Settings(BaseModel):
    model_name: str = "simulated-gemini-2.0-flash"
    strictness: float = 0.75
    dry_run: bool = True
    working_start_hour: int = 9
    working_end_hour: int = 17
    user_timezone_offset_hours: float = -5.0
    client_timezone_offset_hours: float = 0.0
    meeting_duration_minutes: int = 30
    undo_window_seconds: int = 60
    max_tool_calls: int = 6
    max_steps: int = 12
    auto_demo: bool = True


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def parse_iso(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def model_to_dict(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return json.loads(model.json())


def safe_json_dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


def estimate_tokens(obj: Any) -> int:
    return max(1, len(str(obj)) // 4)


def email_domain(addr: str) -> str:
    return addr.split("@")[-1].lower() if "@" in addr else ""


def load_settings() -> Dict[str, Any]:
    settings = Settings(
        strictness=float(os.getenv("ORCH_STRICTNESS", "0.75")),
        dry_run=os.getenv("ORCH_DRY_RUN", "true").strip().lower() not in {"0", "false", "no"},
        user_timezone_offset_hours=float(os.getenv("ORCH_USER_TZ_OFFSET", "-5")),
        client_timezone_offset_hours=float(os.getenv("ORCH_CLIENT_TZ_OFFSET", "0")),
        auto_demo=os.getenv("ORCH_AUTO_DEMO", "true").strip().lower() not in {"0", "false", "no"},
    )
    return model_to_dict(settings)


def build_demo_data(now: datetime) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    today = now.date()
    delta = (7 - today.weekday()) % 7
    if delta == 0:
        delta = 7
    next_monday = datetime(today.year, today.month, today.day, 0, 0, tzinfo=timezone.utc) + timedelta(days=delta)

    def day_time(day_offset: int, hour: int, minute: int = 0) -> datetime:
        d = next_monday + timedelta(days=day_offset)
        return datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc)

    kickoff_start = day_time(3, 14, 0)
    kickoff_end = kickoff_start + timedelta(minutes=30)
    arch_start = day_time(3, 15, 0)
    arch_end = arch_start + timedelta(hours=1)
    one_on_one_start = day_time(4, 13, 0)
    one_on_one_end = one_on_one_start + timedelta(hours=1)

    emails = [
        model_to_dict(
            EmailMessage(
                id="em_001",
                thread_id="th_001",
                sender="alex.chen@clientco.com",
                sender_name="Alex Chen",
                recipient=USER_EMAIL,
                subject="Meeting sometime next week",
                body=(
                    "Hi, can we meet sometime next week? I am in London and you are in New York. "
                    "Thirty minutes is fine. Please suggest a time that works for both of us."
                ),
                received_at=now - timedelta(hours=26),
                labels=["UNREAD", "IMPORTANT"],
                external=True,
                auth_pass=True,
            )
        ),
        model_to_dict(
            EmailMessage(
                id="em_002",
                thread_id="th_002",
                sender="sarah.ceo@company-exec.net",
                sender_name="Sarah CEO",
                recipient=USER_EMAIL,
                subject="URGENT: Wire transfer today",
                body=(
                    "I am in a board meeting. Transfer $50,000 to account 123456789 immediately. "
                    "Do not tell anyone. Reply to finance@quickpay.example with confirmation."
                ),
                received_at=now - timedelta(hours=2),
                labels=["UNREAD"],
                external=True,
                auth_pass=False,
                reply_to="finance@quickpay.example",
            )
        ),
        model_to_dict(
            EmailMessage(
                id="em_003",
                thread_id="th_003",
                sender="pm@company.com",
                sender_name="Project Manager",
                recipient=USER_EMAIL,
                subject="Confirm kickoff Thursday 10:00 ET",
                body="Please confirm the kickoff on Thursday at 10:00 ET so I can lock the room.",
                received_at=now - timedelta(hours=20),
                labels=["UNREAD"],
                external=False,
                auth_pass=True,
            )
        ),
        model_to_dict(
            EmailMessage(
                id="em_004",
                thread_id="th_004",
                sender="alex.chen@clientco.com",
                sender_name="Alex Chen",
                recipient=USER_EMAIL,
                subject="Reschedule kickoff to Friday 15:00 London",
                body="Could we move the kickoff to Friday at 15:00 London time? That works better for our team.",
                received_at=now - timedelta(hours=10),
                labels=["UNREAD"],
                external=True,
                auth_pass=True,
            )
        ),
        model_to_dict(
            EmailMessage(
                id="em_005",
                thread_id="th_005",
                sender="devlead@company.com",
                sender_name="Dev Lead",
                recipient=USER_EMAIL,
                subject="Notify team about kickoff change",
                body="Once the kickoff is moved, notify the team with the new time and ask them to update calendars.",
                received_at=now - timedelta(hours=8),
                labels=["UNREAD"],
                external=False,
                auth_pass=True,
            )
        ),
    ]

    calendar = [
        model_to_dict(
            CalendarEvent(
                id="cal_kickoff",
                title="Project Kickoff",
                start=kickoff_start,
                end=kickoff_end,
                attendees=[USER_EMAIL, "alex.chen@clientco.com", "pm@company.com"],
                timezone_offset_hours=-5.0,
                status="confirmed",
                source="calendar",
            )
        ),
        model_to_dict(
            CalendarEvent(
                id="cal_arch_review",
                title="Architecture Review",
                start=arch_start,
                end=arch_end,
                attendees=[USER_EMAIL, "devlead@company.com"],
                timezone_offset_hours=-5.0,
                status="confirmed",
                source="calendar",
            )
        ),
        model_to_dict(
            CalendarEvent(
                id="cal_one_on_one",
                title="Manager 1:1",
                start=one_on_one_start,
                end=one_on_one_end,
                attendees=[USER_EMAIL, "manager@company.com"],
                timezone_offset_hours=-5.0,
                status="confirmed",
                source="calendar",
            )
        ),
    ]

    return emails, calendar, next_monday.isoformat()


def is_suspicious_financial(email: Dict[str, Any], text: str) -> bool:
    financial = any(keyword in text for keyword in FINANCIAL_KEYWORDS)
    executive_urgent = any(keyword in text for keyword in EXECUTIVE_KEYWORDS)
    reply_to = email.get("reply_to") or ""
    sender = email.get("sender") or ""
    spoof_signals = (
        email.get("auth_pass", True) is False
        or (reply_to and email_domain(reply_to) != email_domain(sender))
    )
    return financial and (executive_urgent or spoof_signals or email.get("external", False))


def is_meeting_request(text: str) -> bool:
    return ("meet" in text or "meeting" in text) and (
        "next week" in text or "sometime" in text or "suggest a time" in text
    )


def is_confirm(text: str) -> bool:
    return "confirm" in text and any(word in text for word in ["kickoff", "meeting", "thursday", "join"])


def is_reschedule(text: str) -> bool:
    return (
        "reschedule" in text
        or ("move" in text and "kickoff" in text)
        or ("change" in text and "kickoff" in text)
    )


def is_notify(text: str) -> bool:
    return "notify" in text and "team" in text


def list_unread_emails(
    label: str = "UNREAD",
    emails: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    if emails is None:
        emails = st.session_state.get("emails", [])
    filtered = [e for e in emails if label in e.get("labels", [])]
    return filtered if filtered else emails


def extract_tasks(email: Dict[str, Any], state: Dict[str, Any]) -> List[Dict[str, Any]]:
    text = f"{email.get('subject', '')} {email.get('body', '')}".lower()
    now = parse_iso(state.get("now")) or utcnow()
    next_monday = parse_iso(state.get("next_monday")) or now
    received = parse_iso(email.get("received_at")) or now
    tasks: List[Dict[str, Any]] = []

    def add_task(title: str, owner: str, deadline: Optional[datetime], priority: str = "medium") -> None:
        task = TaskItem(
            id=f"task_{email['id']}_{len(tasks) + 1}",
            title=title,
            owner=owner,
            deadline=deadline,
            source_email_id=email["id"],
            priority=priority,
        )
        tasks.append(model_to_dict(task))

    if is_suspicious_financial(email, text):
        add_task(
            "Verify urgent payment request out-of-band",
            "security@company.com",
            now + timedelta(hours=1),
            "high",
        )
    elif is_meeting_request(text):
        add_task(
            f"Schedule 30-minute meeting with {email.get('sender_name') or email.get('sender', 'client')}",
            USER_EMAIL,
            next_monday + timedelta(days=4, hours=17),
            "medium",
        )
    elif is_confirm(text):
        add_task("Confirm kickoff attendance", USER_EMAIL, received + timedelta(hours=2), "low")
    elif is_reschedule(text):
        add_task("Reschedule kickoff to proposed time", USER_EMAIL, next_monday + timedelta(days=4, hours=12), "medium")
    elif is_notify(text):
        add_task("Notify team about kickoff change", USER_EMAIL, next_monday + timedelta(days=4, hours=16), "medium")

    return tasks


def slot_display(start: datetime, end: datetime, attendees: List[Dict[str, Any]]) -> str:
    parts = []
    for attendee in attendees:
        offset = float(attendee.get("offset", 0.0))
        label = attendee.get("label", attendee.get("email", "attendee"))
        local_start = start + timedelta(hours=offset)
        local_end = end + timedelta(hours=offset)
        parts.append(f"{label}: {local_start.strftime('%a %b %d %H:%M')}-{local_end.strftime('%H:%M')}")
    return " | ".join(parts)


def find_free_slots(
    attendees: List[Dict[str, Any]],
    duration_minutes: int,
    window_start: datetime,
    window_end: datetime,
    busy_events: List[Dict[str, Any]],
    settings: Dict[str, Any],
) -> List[Dict[str, Any]]:
    slots: List[Dict[str, Any]] = []
    duration = timedelta(minutes=int(duration_minutes))
    start = window_start.replace(hour=0, minute=0, second=0, microsecond=0)
    max_start = window_end - duration
    working_start = int(settings.get("working_start_hour", 9))
    working_end = int(settings.get("working_end_hour", 17))

    while start < max_start:
        end = start + duration
        ok = True

        for attendee in attendees:
            offset = float(attendee.get("offset", 0.0))
            local_start = start + timedelta(hours=offset)
            local_end = end + timedelta(hours=offset)

            if local_end.date() != local_start.date():
                ok = False
                break
            if local_start.hour < working_start:
                ok = False
                break
            if local_end.hour > working_end:
                ok = False
                break
            if local_end.hour == working_end and local_end.minute > 0:
                ok = False
                break

        if ok:
            for event in busy_events:
                event_start = parse_iso(event.get("start"))
                event_end = parse_iso(event.get("end"))
                if event_start and event_end and start < event_end and end > event_start:
                    ok = False
                    break

        if ok:
            slot_id = f"slot_{hashlib.sha256((start.isoformat() + json.dumps(attendees, default=str)).encode()).hexdigest()[:8]}"
            slots.append(
                {
                    "id": slot_id,
                    "start": iso(start),
                    "end": iso(end),
                    "display": slot_display(start, end, attendees),
                }
            )
            if len(slots) >= 5:
                break

        start += timedelta(minutes=30)

    return slots


def draft_reply(email: Dict[str, Any], intent: str, context: Dict[str, Any]) -> str:
    name = email.get("sender_name") or email.get("sender", "there").split("@")[0]

    if intent == "schedule" and context.get("slot_display"):
        return (
            f"Hi {name},\n\n"
            f"Thanks for reaching out. I can do {context['slot_display']}. "
            "Please confirm and I will send a calendar invite.\n\n"
            "Best,\nWorkflow Orchestrator draft for human approval"
        )

    if context.get("event_time"):
        return (
            f"Hi {name},\n\n"
            f"Confirmed: {context['event_time']}.\n\n"
            "Best,\nWorkflow Orchestrator draft for human approval"
        )

    if intent == "delegate" and context.get("team_message"):
        return context["team_message"]

    if intent == "ignore" and context.get("safe_refusal"):
        return (
            "I cannot process payment or transfer requests by email. "
            "This request has been routed to security for out-of-band verification.\n\n"
            "Best,\nWorkflow Orchestrator safe holding draft"
        )

    return (
        "Hi,\n\n"
        "Thanks for the note. I will follow up shortly.\n\n"
        "Best,\nWorkflow Orchestrator draft for human approval"
    )


def propose_action(
    action_type: str,
    payload: Dict[str, Any],
    risk: str = "medium",
    reasoning: str = "",
    evidence_ids: Optional[List[str]] = None,
    needs_approval: bool = True,
) -> Dict[str, Any]:
    evidence_ids = evidence_ids or []
    payload_text = json.dumps(payload, default=str).lower()

    if action_type in BLOCKED_TYPES or any(
        token in payload_text for token in ["transfer_funds", "wire_transfer", "payment", "gift_card", "iban", "account_number"]
    ):
        original_type = action_type
        action_type = "flag_for_security_review"
        payload = {
            "original_type": original_type,
            "original_payload": payload,
            "recommended_action": "verify_out_of_band",
        }
        risk = "high"
        reasoning = reasoning or "Blocked potentially fraudulent financial action; escalated for human security review."

    if action_type in SIDE_EFFECT_TYPES:
        needs_approval = True

    recipients: List[str] = []
    if isinstance(payload.get("to"), str):
        recipients.append(payload["to"])
    if isinstance(payload.get("attendees"), list):
        recipients.extend([x for x in payload["attendees"] if isinstance(x, str)])

    if any(email_domain(r) and email_domain(r) != email_domain(USER_EMAIL) for r in recipients):
        risk = "high"

    if risk not in {"low", "medium", "high"}:
        risk = "medium"

    action = ProposedAction(
        id=f"pa_{uuid.uuid4().hex[:10]}",
        type=action_type,
        payload=payload,
        risk=risk,
        needs_approval=True,
        status="pending",
        reasoning=reasoning,
        evidence_ids=evidence_ids,
        created_at=utcnow(),
    )
    return model_to_dict(action)


def can_execute(action: Dict[str, Any]) -> Tuple[bool, str]:
    if action.get("type") in BLOCKED_TYPES:
        return False, "blocked action type"
    if action.get("needs_approval", True) and action.get("status") not in {"approved", "executed"}:
        return False, "explicit human approval required"
    if action.get("status") in {"rejected", "undone", "expired", "pending"}:
        return False, "action is not active"
    return True, "simulated execution allowed"


def apply_guardrails_to_result(
    result: Dict[str, Any],
    email_map: Dict[str, Dict[str, Any]],
    settings: Dict[str, Any],
) -> Dict[str, Any]:
    email = email_map.get(result.get("email_id", ""), {})
    for action in result.get("proposed_actions", []):
        if action.get("type") in SIDE_EFFECT_TYPES:
            action["needs_approval"] = True
            action["status"] = "pending"
        if email.get("external") and action.get("type") in {
            "send_reply",
            "send_draft_reply",
            "send_team_notification",
            "create_calendar_event",
            "update_calendar_event",
            "draft_reply",
        }:
            action["risk"] = "high"
    return result


def plan_one_email(
    email: Dict[str, Any],
    state: Dict[str, Any],
    prior_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    settings = state["settings"]
    text = f"{email.get('subject', '')} {email.get('body', '')}".lower()
    tasks = extract_tasks(email, state)
    actions: List[Dict[str, Any]] = []
    conflicts: List[str] = []
    reasoning = ""
    intent: Literal["schedule", "reply", "delegate", "ignore"] = "ignore"

    now = parse_iso(state.get("now")) or utcnow()
    next_monday = parse_iso(state.get("next_monday")) or now

    if is_suspicious_financial(email, text):
        intent = "ignore"
        reasoning = (
            "Potential spoofed executive payment request. The agent will not draft a payment instruction "
            "or reply with banking details. It proposes a security review and an optional safe holding draft."
        )
        actions.append(
            propose_action(
                "flag_for_security_review",
                {
                    "email_id": email["id"],
                    "sender": email.get("sender"),
                    "subject": email.get("subject"),
                    "recommended_action": "verify_out_of_band",
                    "do_not_reply": True,
                },
                "high",
                reasoning,
                [email["id"]],
            )
        )
        safe_body = draft_reply(email, "ignore", {"safe_refusal": True})
        actions.append(
            propose_action(
                "draft_reply",
                {
                    "to": email.get("sender"),
                    "subject": f"Re: {email.get('subject', '')}",
                    "body": safe_body,
                    "do_not_send_until_verified": True,
                },
                "high",
                "Optional safe holding reply only after security review.",
                [email["id"]],
            )
        )
        result = RunResult(
            email_id=email["id"],
            intent=intent,
            tasks=tasks,
            proposed_actions=actions,
            conflicts=conflicts,
            reasoning=reasoning,
        )
        return model_to_dict(result)

    if is_meeting_request(text):
        intent = "schedule"
        attendees = [
            {
                "email": USER_EMAIL,
                "offset": float(settings.get("user_timezone_offset_hours", -5.0)),
                "label": "New York",
            },
            {
                "email": email.get("sender", "client@example.com"),
                "offset": float(settings.get("client_timezone_offset_hours", 0.0)),
                "label": "London",
            },
        ]
        window_start = next_monday
        window_end = next_monday + timedelta(days=7)
        busy = state.get("calendar", []) + state.get("scheduled_events", [])
        slots = find_free_slots(
            attendees=attendees,
            duration_minutes=int(settings.get("meeting_duration_minutes", 30)),
            window_start=window_start,
            window_end=window_end,
            busy_events=busy,
            settings=settings,
        )

        if slots:
            chosen = slots[0]
            reasoning = f"Found mutually free slot {chosen['display']} after checking working hours and existing calendar events."
            payload = {
                "title": f"Meeting with {email.get('sender_name') or email.get('sender', 'client')}",
                "start": chosen["start"],
                "end": chosen["end"],
                "attendees": [a["email"] for a in attendees],
                "timezone": "UTC",
                "source_email_id": email["id"],
                "slot_id": chosen["id"],
            }
            actions.append(
                propose_action(
                    "create_calendar_event",
                    payload,
                    "high",
                    reasoning,
                    [email["id"], chosen["id"]],
                )
            )
            body = draft_reply(email, intent, {"slot_display": chosen["display"]})
            actions.append(
                propose_action(
                    "send_reply",
                    {
                        "to": email.get("sender"),
                        "subject": f"Re: {email.get('subject', '')}",
                        "body": body,
                    },
                    "high",
                    "Reply with proposed time only after human approval.",
                    [email["id"]],
                )
            )
        else:
            conflicts.append("No mutually free slot in the requested window.")
            reasoning = "Could not find a slot that respects both time zones and working hours."
            body = draft_reply(email, intent, {})
            actions.append(
                propose_action(
                    "draft_reply",
                    {
                        "to": email.get("sender"),
                        "subject": f"Re: {email.get('subject', '')}",
                        "body": "I could not find a mutually free slot in the requested window. Please suggest another time.",
                    },
                    "medium",
                    "Ask for alternatives without exposing calendar details.",
                    [email["id"]],
                )
            )

        result = RunResult(
            email_id=email["id"],
            intent=intent,
            tasks=tasks,
            proposed_actions=actions,
            conflicts=conflicts,
            reasoning=reasoning,
        )
        return model_to_dict(result)

    if is_confirm(text):
        intent = "reply"
        kickoff = next((ev for ev in state.get("calendar", []) if "kickoff" in ev.get("title", "").lower()), None)
        if kickoff:
            start = parse_iso(kickoff.get("start")) or now
            local = start + timedelta(hours=float(settings.get("user_timezone_offset_hours", -5.0)))
            event_time = local.strftime("%A %B %d at %H:%M local time")
            body = draft_reply(email, intent, {"event_time": event_time})
            reasoning = "Confirmed existing kickoff time from calendar."
            actions.append(
                propose_action(
                    "send_reply",
                    {
                        "to": email.get("sender"),
                        "subject": f"Re: {email.get('subject', '')}",
                        "body": body,
                    },
                    "medium",
                    reasoning,
                    [email["id"], kickoff.get("id", "")],
                )
            )
        else:
            conflicts.append("No kickoff event found.")
            reasoning = "Could not find the referenced kickoff event."

        result = RunResult(
            email_id=email["id"],
            intent=intent,
            tasks=tasks,
            proposed_actions=actions,
            conflicts=conflicts,
            reasoning=reasoning,
        )
        return model_to_dict(result)

    if is_reschedule(text):
        intent = "schedule"
        requested_start = next_monday + timedelta(days=4, hours=15)
        requested_end = requested_start + timedelta(minutes=int(settings.get("meeting_duration_minutes", 30)))
        busy = [ev for ev in state.get("calendar", []) + state.get("scheduled_events", []) if ev.get("id") != "cal_kickoff"]

        conflicting = False
        for event in busy:
            event_start = parse_iso(event.get("start"))
            event_end = parse_iso(event.get("end"))
            if event_start and event_end and requested_start < event_end and requested_end > event_start:
                conflicting = True
                break

        attendees = [
            {
                "email": USER_EMAIL,
                "offset": float(settings.get("user_timezone_offset_hours", -5.0)),
                "label": "New York",
            },
            {
                "email": email.get("sender", "client@example.com"),
                "offset": float(settings.get("client_timezone_offset_hours", 0.0)),
                "label": "London",
            },
        ]

        if not conflicting:
            chosen = {
                "id": f"slot_req_{hashlib.sha256(requested_start.isoformat().encode()).hexdigest()[:8]}",
                "start": iso(requested_start),
                "end": iso(requested_end),
                "display": slot_display(requested_start, requested_end, attendees),
            }
        else:
            slots = find_free_slots(
                attendees=attendees,
                duration_minutes=int(settings.get("meeting_duration_minutes", 30)),
                window_start=next_monday,
                window_end=next_monday + timedelta(days=7),
                busy_events=busy,
                settings=settings,
            )
            chosen = slots[0] if slots else None

        if chosen:
            reasoning = f"Reschedule proposal uses {chosen['display']} after avoiding conflicts."
            payload = {
                "event_id": "cal_kickoff",
                "title": "Project Kickoff (rescheduled)",
                "start": chosen["start"],
                "end": chosen["end"],
                "attendees": [a["email"] for a in attendees],
                "timezone": "UTC",
                "source_email_id": email["id"],
            }
            actions.append(
                propose_action(
                    "update_calendar_event",
                    payload,
                    "high",
                    reasoning,
                    [email["id"], chosen["id"]],
                )
            )
            body = draft_reply(email, intent, {"slot_display": chosen["display"]})
            actions.append(
                propose_action(
                    "send_reply",
                    {
                        "to": email.get("sender"),
                        "subject": f"Re: {email.get('subject', '')}",
                        "body": body,
                    },
                    "high",
                    "Confirm reschedule only after human approval.",
                    [email["id"]],
                )
            )
        else:
            conflicts.append("No conflict-free reschedule slot found.")
            reasoning = "Requested reschedule conflicts with existing commitments and no alternative slot was found."

        result = RunResult(
            email_id=email["id"],
            intent=intent,
            tasks=tasks,
            proposed_actions=actions,
            conflicts=conflicts,
            reasoning=reasoning,
        )
        return model_to_dict(result)

    if is_notify(text):
        intent = "delegate"
        new_time = None

        for res in reversed(prior_results):
            for action in res.get("proposed_actions", []):
                if action.get("type") == "update_calendar_event":
                    start = parse_iso(action.get("payload", {}).get("start"))
                    if start:
                        local = start + timedelta(hours=float(settings.get("user_timezone_offset_hours", -5.0)))
                        new_time = local.strftime("%A %B %d at %H:%M local time")
                        break
            if new_time:
                break

        if not new_time:
            kickoff = next((ev for ev in state.get("calendar", []) if "kickoff" in ev.get("title", "").lower()), None)
            if kickoff:
                start = parse_iso(kickoff.get("start"))
                if start:
                    local = start + timedelta(hours=float(settings.get("user_timezone_offset_hours", -5.0)))
                    new_time = local.strftime("%A %B %d at %H:%M local time")

        if new_time:
            team_message = (
                "Team,\n\n"
                f"The kickoff has been moved to {new_time}. Please update your calendars.\n\n"
                "Best,\nWorkflow Orchestrator draft for human approval"
            )
            reasoning = "Generated team notification based on latest reschedule proposal or existing kickoff."
            actions.append(
                propose_action(
                    "send_team_notification",
                    {
                        "to": "team@company.com",
                        "subject": "Kickoff time update",
                        "body": team_message,
                    },
                    "medium",
                    reasoning,
                    [email["id"]],
                )
            )
        else:
            conflicts.append("No kickoff time found for notification.")
            reasoning = "Could not determine the kickoff time to notify the team."

        result = RunResult(
            email_id=email["id"],
            intent=intent,
            tasks=tasks,
            proposed_actions=actions,
            conflicts=conflicts,
            reasoning=reasoning,
        )
        return model_to_dict(result)

    reasoning = "No clear actionable request; no side-effect action proposed."
    result = RunResult(
        email_id=email["id"],
        intent="ignore",
        tasks=tasks,
        proposed_actions=[],
        conflicts=[],
        reasoning=reasoning,
    )
    return model_to_dict(result)


def append_audit(event: str, detail: str = "", action_id: str = "", actor: str = "system") -> None:
    log = st.session_state.setdefault("audit_log", [])
    prev = log[-1]["hash"] if log else "genesis"
    base = {
        "timestamp": iso(utcnow()),
        "run_id": st.session_state.get("run_id", ""),
        "actor": actor,
        "event": event,
        "action_id": action_id,
        "detail": detail,
    }
    entry_hash = hashlib.sha256((prev + json.dumps(base, sort_keys=True, default=str)).encode()).hexdigest()[:16]
    base["hash"] = entry_hash
    base["prev_hash"] = prev
    log.append(base)


def trace_node(
    node: str,
    duration: float,
    tokens_in: int = 0,
    tokens_out: int = 0,
    status: str = "ok",
    detail: str = "",
) -> None:
    entry = {
        "timestamp": iso(utcnow()),
        "run_id": st.session_state.get("run_id", ""),
        "node": node,
        "duration_ms": round(duration * 1000, 1),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": round((tokens_in + tokens_out) * 0.00000025, 6),
        "status": status,
        "detail": detail,
    }
    st.session_state.setdefault("trace", []).append(entry)


def guardrail_self_test() -> List[Dict[str, Any]]:
    tests: List[Dict[str, Any]] = []

    external = propose_action(
        "send_reply",
        {"to": "outside@example.com", "body": "hello"},
        "low",
        "",
        [],
    )
    tests.append(
        {
            "check": "External recipient is forced to high risk",
            "passed": external["risk"] == "high" and external["needs_approval"] is True,
        }
    )

    blocked = propose_action(
        "transfer_funds",
        {"amount": 50000, "account": "123456789"},
        "low",
        "",
        [],
    )
    tests.append(
        {
            "check": "Financial transfer is sanitized to security review",
            "passed": blocked["type"] == "flag_for_security_review" and blocked["risk"] == "high",
        }
    )

    executable, _ = can_execute(external)
    tests.append(
        {
            "check": "Unapproved action cannot execute",
            "passed": executable is False,
        }
    )

    calendar_action = propose_action(
        "create_calendar_event",
        {
            "start": iso(utcnow()),
            "end": iso(utcnow() + timedelta(minutes=30)),
            "attendees": [USER_EMAIL],
        },
        "medium",
        "",
        [],
    )
    calendar_action["status"] = "approved"
    calendar_action["undo_expires_at"] = iso(utcnow() + timedelta(seconds=60))
    tests.append(
        {
            "check": "Approved action receives undo window",
            "passed": bool(calendar_action.get("undo_expires_at")),
        }
    )

    draft = propose_action(
        "draft_reply",
        {"to": USER_EMAIL, "body": "draft only"},
        "low",
        "",
        [],
    )
    tests.append(
        {
            "check": "Even drafts require approval in this demo",
            "passed": draft["needs_approval"] is True,
        }
    )

    return tests


def init_state() -> None:
    if st.session_state.get("initialized"):
        return

    now = utcnow()
    settings = load_settings()
    emails, calendar, next_monday = build_demo_data(now)

    st.session_state.initialized = True
    st.session_state.now = iso(now)
    st.session_state.settings = settings
    st.session_state.emails = emails
    st.session_state.calendar = calendar
    st.session_state.next_monday = next_monday
    st.session_state.results = []
    st.session_state.pending_actions = []
    st.session_state.tasks = []
    st.session_state.scheduled_events = []
    st.session_state.audit_log = []
    st.session_state.trace = []
    st.session_state.run_id = uuid.uuid4().hex[:12]
    st.session_state.scenario = "All scenarios"
    st.session_state.auto_demo = bool(settings.get("auto_demo", True))
    st.session_state.demo_clip = None
    st.session_state.degraded = False
    st.session_state.schema_valid_rate = 1.0
    st.session_state.guardrail_tests = guardrail_self_test()
    st.session_state.uploaded_email_name = None
    st.session_state.uploaded_calendar_name = None

    append_audit("app_initialized", "Demo data loaded and guardrail self-test completed")


def get_scenario_emails(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    scenario = state.get("scenario", "All scenarios")
    if scenario == "Custom upload":
        return state.get("emails", [])
    ids = SCENARIOS.get(scenario, [])
    return [e for e in state.get("emails", []) if e.get("id") in ids]


def run_simulation(parent: Any) -> None:
    state = st.session_state
    settings = state["settings"]

    state["results"] = []
    state["pending_actions"] = []
    state["tasks"] = []
    state["scheduled_events"] = []
    state["trace"] = []
    state["demo_clip"] = None
    state["degraded"] = False
    state["run_id"] = uuid.uuid4().hex[:12]

    emails_all = get_scenario_emails(state)
    messages = list_unread_emails("UNREAD", emails_all)

    with parent:
        st.markdown("#### Pipeline status")
        status = st.empty()
        progress = st.progress(0.0)
        log_placeholder = st.empty()

    nodes = ["ingest", "perceive", "plan", "guardrail", "human_gate", "finalize"]
    results: List[Dict[str, Any]] = []
    all_tasks: List[Dict[str, Any]] = []
    all_actions: List[Dict[str, Any]] = []

    for idx, node in enumerate(nodes):
        t0 = time.perf_counter()
        tokens_in = 0
        tokens_out = 0
        detail = ""

        if node == "ingest":
            tokens_in = sum(estimate_tokens(e.get("subject", "") + e.get("body", "")) for e in messages)
            detail = f"{len(messages)} email(s), {len(state.get('calendar', []))} calendar event(s)"

        elif node == "perceive":
            for email in messages:
                extracted = extract_tasks(email, state)
                all_tasks.extend(extracted)
            tokens_in = sum(estimate_tokens(t) for t in all_tasks)
            detail = f"{len(all_tasks)} task(s) extracted"

        elif node == "plan":
            email_map = {e["id"]: e for e in messages}
            for email in messages:
                result = plan_one_email(email, state, results)
                result = apply_guardrails_to_result(result, email_map, settings)
                results.append(result)
                all_actions.extend(result.get("proposed_actions", []))
            tokens_out = sum(estimate_tokens(r.get("reasoning", "")) for r in results)
            detail = f"{len(results)} plan(s), {len(all_actions)} proposed action(s)"

        elif node == "guardrail":
            for result in results:
                for action in result.get("proposed_actions", []):
                    action["needs_approval"] = True
                    if action.get("type") in BLOCKED_TYPES:
                        action["type"] = "flag_for_security_review"
                        action["risk"] = "high"
            state["degraded"] = any(
                "No mutually free slot" in conflict or "No conflict-free" in conflict
                for result in results
                for conflict in result.get("conflicts", [])
            )
            detail = "All side-effect actions gated; blocked financial actions sanitized"

        elif node == "human_gate":
            state["pending_actions"] = all_actions
            pending_count = sum(1 for a in all_actions if a.get("status") == "pending")
            detail = f"{pending_count} action(s) waiting for approval"

        else:
            state["results"] = results
            state["tasks"] = all_tasks
            detail = "Schema-valid output ready"

        duration = time.perf_counter() - t0
        trace_node(node, duration, tokens_in, tokens_out, "ok", detail)

        status.markdown(f"**{node}** ✅ {detail}")
        progress.progress(float((idx + 1) / len(nodes)))
        if state.get("trace"):
            log_placeholder.dataframe(pd.DataFrame(state["trace"]), use_container_width=True)

        time.sleep(0.22)

    append_audit("simulation_complete", f"Run {state['run_id']} completed")


def refresh_action_statuses() -> None:
    state = st.session_state
    now = utcnow()
    for action in state.get("pending_actions", []):
        if action.get("status") == "approved" and action.get("undo_expires_at"):
            expires = parse_iso(action.get("undo_expires_at"))
            if expires and expires <= now:
                action["status"] = "executed"
                append_audit("undo_window_expired", "Action finalized in simulation", action.get("id", ""), "system")


def build_final_output() -> List[Dict[str, Any]]:
    state = st.session_state
    actions_by_id = {a["id"]: a for a in state.get("pending_actions", [])}
    output: List[Dict[str, Any]] = []
    valid = 0
    total = len(state.get("results", []))

    for result in state.get("results", []):
        row = dict(result)
        row["proposed_actions"] = [
            actions_by_id.get(action.get("id"), action)
            for action in row.get("proposed_actions", [])
        ]
        try:
            validated = RunResult(**row)
            output.append(model_to_dict(validated))
            valid += 1
        except ValidationError as exc:
            state["degraded"] = True
            row["reasoning"] = f"{row.get('reasoning', '')} [validation fallback: {exc}]".strip()
            output.append(row)

    state["schema_valid_rate"] = valid / total if total else 1.0
    return output


def build_events_table() -> pd.DataFrame:
    state = st.session_state
    rows: List[Dict[str, Any]] = []

    for email in state.get("emails", []):
        rows.append(
            {
                "type": "email",
                "id": email.get("id", ""),
                "title": email.get("subject", ""),
                "detail": email.get("sender", ""),
                "status": "unread" if "UNREAD" in email.get("labels", []) else "read",
                "time": email.get("received_at", ""),
            }
        )

    for event in state.get("calendar", []):
        rows.append(
            {
                "type": "calendar",
                "id": event.get("id", ""),
                "title": event.get("title", ""),
                "detail": ", ".join(event.get("attendees", [])),
                "status": event.get("status", "confirmed"),
                "time": event.get("start", ""),
            }
        )

    for action in state.get("pending_actions", []):
        rows.append(
            {
                "type": "action",
                "id": action.get("id", ""),
                "title": action.get("type", ""),
                "detail": action.get("risk", ""),
                "status": action.get("status", ""),
                "time": action.get("created_at", ""),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["time_parsed"] = df["time"].apply(parse_iso)
    df = df.sort_values("time_parsed", na_position="first").drop(columns=["time_parsed"])
    return df


def find_action(action_id: str) -> Optional[Dict[str, Any]]:
    for action in st.session_state.get("pending_actions", []):
        if action.get("id") == action_id:
            return action
    return None


def is_undo_available(action: Dict[str, Any]) -> bool:
    if action.get("status") not in {"approved", "executed"}:
        return False
    expires = parse_iso(action.get("undo_expires_at"))
    return bool(expires and expires > utcnow())


def approve_action(action_id: str, note: str = "") -> None:
    action = find_action(action_id)
    if not action or action.get("status") != "pending":
        return

    action["status"] = "approved"
    action["decided_at"] = iso(utcnow())
    action["note"] = note
    action["undo_expires_at"] = iso(
        utcnow() + timedelta(seconds=int(st.session_state["settings"].get("undo_window_seconds", 60)))
    )

    ok, reason = can_execute(action)
    if not ok:
        action["status"] = "pending"
        action["undo_expires_at"] = None
        st.error(f"Approval blocked by guardrail: {reason}")
        append_audit("approval_blocked", reason, action_id, "guardrail")
        return

    if action.get("type") in {"create_calendar_event", "update_calendar_event"}:
        payload = action.get("payload", {})
        if payload.get("start") and payload.get("end"):
            scheduled_event = {
                "id": f"sched_{action_id}",
                "title": payload.get("title", "Scheduled event"),
                "start": payload.get("start"),
                "end": payload.get("end"),
                "attendees": payload.get("attendees", []),
                "timezone_offset_hours": float(st.session_state["settings"].get("user_timezone_offset_hours", -5.0)),
                "status": "approved",
                "source": "approved_action",
                "action_id": action_id,
            }
            st.session_state.setdefault("scheduled_events", []).append(scheduled_event)

    st.session_state["demo_clip"] = None
    append_audit("action_approved", note or "Approved in UI", action_id, "human")


def reject_action(action_id: str, note: str = "") -> None:
    action = find_action(action_id)
    if not action or action.get("status") != "pending":
        return

    action["status"] = "rejected"
    action["decided_at"] = iso(utcnow())
    action["note"] = note
    action["undo_expires_at"] = None
    st.session_state["demo_clip"] = None
    append_audit("action_rejected", note or "Rejected in UI", action_id, "human")


def undo_action(action_id: str) -> None:
    action = find_action(action_id)
    if not action or not is_undo_available(action):
        return

    action["status"] = "undone"
    action["decided_at"] = iso(utcnow())
    action["note"] = "Undone within safety window"
    action["undo_expires_at"] = None

    st.session_state["scheduled_events"] = [
        ev for ev in st.session_state.get("scheduled_events", []) if ev.get("action_id") != action_id
    ]

    st.session_state["demo_clip"] = None
    append_audit("action_undone", "Undo window used", action_id, "human")


def edit_action(action_id: str, payload_text: str, note: str = "") -> None:
    action = find_action(action_id)
    if not action or action.get("status") != "pending":
        return

    try:
        payload = json.loads(payload_text)
        if not isinstance(payload, dict):
            raise ValueError("Payload must be a JSON object.")
    except Exception as exc:
        st.error(f"Invalid JSON: {exc}")
        append_audit("action_edit_failed", str(exc), action_id, "human")
        return

    fresh = propose_action(
        action.get("type", "draft_reply"),
        payload,
        action.get("risk", "medium"),
        action.get("reasoning", ""),
        action.get("evidence_ids", []),
    )

    action["type"] = fresh["type"]
    action["payload"] = fresh["payload"]
    action["risk"] = fresh["risk"]
    action["needs_approval"] = True
    action["note"] = note or "Edited in UI"

    st.session_state["demo_clip"] = None
    append_audit("action_edited", note or "Edited in UI", action_id, "human")


def render_action_card(action: Dict[str, Any]) -> None:
    st.markdown("---")
    c1, c2, c3 = st.columns([4, 1, 2])

    status_icon = {
        "pending": "🟡",
        "approved": "🟢",
        "executed": "✅",
        "rejected": "🔴",
        "undone": "↩️",
        "edited": "✏️",
        "expired": "⌛",
    }.get(action.get("status", "pending"), "⚪")

    with c1:
        st.markdown(
            f"**{status_icon} {action.get('type', '')}**  \n"
            f"Risk: `{action.get('risk', '')}`  \n"
            f"Status: `{action.get('status', '')}`  \n"
            f"Needs approval: `{action.get('needs_approval', True)}`"
        )
        st.caption(action.get("reasoning", ""))

    with c2:
        with st.expander("Payload"):
            st.json(action.get("payload", {}))

    with c3:
        if action.get("status") == "pending":
            if st.button("Approve", key=f"approve_{action['id']}"):
                approve_action(action["id"], "")
                st.rerun()
            if st.button("Reject", key=f"reject_{action['id']}"):
                reject_action(action["id"], "Rejected in UI")
                st.rerun()
        elif is_undo_available(action):
            if st.button("Undo", key=f"undo_{action['id']}"):
                undo_action(action["id"])
                st.rerun()

    with st.expander("Edit payload"):
        if action.get("status") == "pending":
            edited = st.text_area(
                "JSON payload",
                json.dumps(action.get("payload", {}), indent=2, default=str),
                key=f"edit_{action['id']}",
            )
            if st.button("Save edit", key=f"save_{action['id']}"):
                edit_action(action["id"], edited, "Edited in UI")
                st.rerun()
        else:
            st.caption("Editing is disabled after a decision.")


def render_sidebar() -> None:
    state = st.session_state

    st.sidebar.title("Workflow Orchestrator")
    st.sidebar.caption("Simulated Gemini + Google tools. No API key required.")

    scenario_options = list(SCENARIOS.keys()) + ["Custom upload"]
    if state.get("scenario") not in scenario_options:
        scenario_options.append(state.get("scenario", "All scenarios"))

    index = scenario_options.index(state.get("scenario", "All scenarios"))
    scenario = st.sidebar.selectbox("Scenario", scenario_options, index=index)
    if scenario != state.get("scenario"):
        state["scenario"] = scenario
        state["auto_demo"] = True

    st.sidebar.divider()
    st.sidebar.subheader("Controls")

    strictness = st.sidebar.slider(
        "Strictness",
        min_value=0.0,
        max_value=1.0,
        value=float(state["settings"].get("strictness", 0.75)),
        step=0.05,
    )
    state["settings"]["strictness"] = strictness

    dry_run = st.sidebar.checkbox(
        "Dry run",
        value=bool(state["settings"].get("dry_run", True)),
    )
    state["settings"]["dry_run"] = dry_run

    user_tz = st.sidebar.number_input(
        "User UTC offset",
        value=float(state["settings"].get("user_timezone_offset_hours", -5.0)),
        step=0.5,
        format="%.1f",
    )
    client_tz = st.sidebar.number_input(
        "Client UTC offset",
        value=float(state["settings"].get("client_timezone_offset_hours", 0.0)),
        step=0.5,
        format="%.1f",
    )
    state["settings"]["user_timezone_offset_hours"] = float(user_tz)
    state["settings"]["client_timezone_offset_hours"] = float(client_tz)

    work_start = st.sidebar.number_input(
        "Working start hour",
        min_value=0,
        max_value=23,
        value=int(state["settings"].get("working_start_hour", 9)),
    )
    work_end = st.sidebar.number_input(
        "Working end hour",
        min_value=1,
        max_value=24,
        value=int(state["settings"].get("working_end_hour", 17)),
    )
    state["settings"]["working_start_hour"] = int(work_start)
    state["settings"]["working_end_hour"] = int(work_end)

    if st.sidebar.button("Run simulation"):
        state["auto_demo"] = True

    if st.sidebar.button("Reset demo"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

    st.sidebar.divider()
    st.sidebar.subheader("Upload JSON (optional)")

    email_file = st.sidebar.file_uploader("Emails", type=["json"])
    if email_file is not None:
        handle_email_upload(email_file)

    calendar_file = st.sidebar.file_uploader("Calendar", type=["json"])
    if calendar_file is not None:
        handle_calendar_upload(calendar_file)


def handle_email_upload(file: Any) -> None:
    state = st.session_state
    if state.get("uploaded_email_name") == file.name:
        return

    if getattr(file, "size", 0) and file.size > 2_000_000:
        st.sidebar.error("Email file too large. Max 2 MB.")
        return

    try:
        data = json.load(file)
        if not isinstance(data, list):
            raise ValueError("Expected a JSON list.")
        if len(data) > 200:
            raise ValueError("Max 200 emails for demo safety.")

        validated: List[Dict[str, Any]] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise ValueError(f"Item {i} is not an object.")
            item.setdefault("id", f"upload_em_{i + 1}")
            item.setdefault("thread_id", item["id"])
            item.setdefault("sender", "unknown@example.com")
            item.setdefault("subject", "(no subject)")
            item.setdefault("body", "")
            item.setdefault("received_at", iso(utcnow()))
            validated.append(model_to_dict(EmailMessage(**item)))

        state["emails"] = validated
        state["scenario"] = "Custom upload"
        state["uploaded_email_name"] = file.name
        state["auto_demo"] = True
        st.sidebar.success(f"Loaded {len(validated)} email(s).")
    except Exception as exc:
        st.sidebar.error(f"Could not parse email JSON: {exc}")


def handle_calendar_upload(file: Any) -> None:
    state = st.session_state
    if state.get("uploaded_calendar_name") == file.name:
        return

    if getattr(file, "size", 0) and file.size > 2_000_000:
        st.sidebar.error("Calendar file too large. Max 2 MB.")
        return

    try:
        data = json.load(file)
        if not isinstance(data, list):
            raise ValueError("Expected a JSON list.")
        if len(data) > 200:
            raise ValueError("Max 200 calendar events for demo safety.")

        validated: List[Dict[str, Any]] = []
        now = utcnow()
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise ValueError(f"Item {i} is not an object.")
            item.setdefault("id", f"upload_cal_{i + 1}")
            item.setdefault("title", "(untitled event)")
            item.setdefault("start", iso(now))
            item.setdefault("end", iso(now + timedelta(minutes=30)))
            item.setdefault("attendees", [USER_EMAIL])
            validated.append(model_to_dict(CalendarEvent(**item)))

        state["calendar"] = validated
        state["uploaded_calendar_name"] = file.name
        state["auto_demo"] = True
        st.sidebar.success(f"Loaded {len(validated)} calendar event(s).")
    except Exception as exc:
        st.sidebar.error(f"Could not parse calendar JSON: {exc}")


def generate_demo_clip() -> bytes:
    state = st.session_state
    width, height = 960, 540
    bg = "#0f172a"
    fg = "#f8fafc"
    muted = "#94a3b8"
    accent = "#38bdf8"
    warn = "#f97316"
    ok = "#22c55e"

    font = ImageFont.load_default()
    images: List[Image.Image] = []

    def new_frame(title: str, subtitle: str = "") -> Tuple[Image.Image, ImageDraw.ImageDraw]:
        img = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, width, 76], fill="#111827")
        draw.text((24, 18), title, fill=fg, font=font)
        if subtitle:
            draw.text((24, 44), subtitle, fill=muted, font=font)
        return img, draw

    def bullets(draw: ImageDraw.ImageDraw, x: int, y: int, lines: List[str], color: str = fg) -> int:
        for line in lines:
            draw.text((x, y), line, fill=color, font=font)
            y += 24
        return y

    img, draw = new_frame("Workflow Orchestrator Agent", "Live demo: emails, calendar, human approval")
    bullets(
        draw,
        30,
        110,
        [
            "• Ingests unread emails and calendar events",
            "• Extracts tasks, drafts, and schedule proposals",
            "• Proposes actions but never executes without approval",
            "• Applies time-zone, spoofing, and privacy guardrails",
            "• Writes an auditable trail of every decision",
        ],
    )
    images.append(img)

    img, draw = new_frame("1. Inbox ingestion", f"Run {state.get('run_id', '')}")
    lines = []
    for email in state.get("emails", [])[:5]:
        kind = "external" if email.get("external") else "internal"
        lines.append(f"{email.get('id', '')}: {email.get('subject', '')} ({kind})")
    bullets(draw, 30, 110, lines or ["No emails"])
    images.append(img)

    img, draw = new_frame("2. Task extraction")
    lines = [f"{t.get('title', '')} -> {t.get('owner', '')}" for t in state.get("tasks", [])[:6]]
    bullets(draw, 30, 110, lines or ["No tasks"])
    images.append(img)

    img, draw = new_frame("3. Planning intents")
    lines = []
    for result in state.get("results", [])[:5]:
        reason = str(result.get("reasoning", ""))[:68]
        lines.append(f"{result.get('email_id', '')}: {result.get('intent', '')} — {reason}")
    bullets(draw, 30, 110, lines or ["No plans"])
    images.append(img)

    img, draw = new_frame("4. Human approval gate", "All side-effect actions are pending")
    y = 110
    for action in state.get("pending_actions", [])[:4]:
        draw.rectangle([30, y, width - 30, y + 78], outline=accent, width=2)
        draw.text((42, y + 12), f"{action.get('type', '')} | risk {action.get('risk', '')} | {action.get('status', '')}", fill=fg, font=font)
        draw.text((42, y + 42), "Approve     Edit     Reject", fill=warn, font=font)
        y += 92
    images.append(img)

    img, draw = new_frame("5. Approval simulation", "Demo clip shows a user approving a safe action")
    draw.rectangle([30, 110, 280, 170], fill="#1d4ed8")
    draw.text((82, 132), "Approve", fill=fg, font=font)
    draw.text((310, 132), "-> status: approved", fill=ok, font=font)
    draw.text((30, 210), "Calendar ghost event becomes scheduled in simulation.", fill=fg, font=font)
    draw.text((30, 240), "60-second undo window starts.", fill=fg, font=font)
    draw.text((30, 290), "No real Google API call is made in this no-key demo.", fill=muted, font=font)
    images.append(img)

    img, draw = new_frame("6. Audit trail")
    lines = []
    for entry in state.get("audit_log", [])[-6:]:
        ts = str(entry.get("timestamp", ""))[11:19]
        lines.append(f"{ts} {entry.get('event', '')} {entry.get('action_id', '')}")
    bullets(draw, 30, 110, lines or ["No audit entries"])
    images.append(img)

    img, draw = new_frame("7. Outcome", "Scheduled actions and drafts ready for real API integration")
    pending = sum(1 for a in state.get("pending_actions", []) if a.get("status") == "pending")
    approved = sum(1 for a in state.get("pending_actions", []) if a.get("status") in {"approved", "executed"})
    high = sum(1 for a in state.get("pending_actions", []) if a.get("risk") == "high")
    bullets(
        draw,
        30,
        110,
        [
            f"Emails processed: {len(state.get('results', []))}",
            f"Pending approvals: {pending}",
            f"Approved/executed: {approved}",
            f"High-risk items: {high}",
            "Safety rule: no unapproved side effects.",
        ],
    )
    images.append(img)

    buf = io.BytesIO()
    images[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=images[1:],
        duration=900,
        loop=0,
    )
    buf.seek(0)
    return buf.getvalue()


def render_live_summary() -> None:
    state = st.session_state
    output = build_final_output()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Emails processed", len(output))
    c2.metric("Pending approvals", sum(1 for a in state.get("pending_actions", []) if a.get("status") == "pending"))
    c3.metric("High-risk items", sum(1 for a in state.get("pending_actions", []) if a.get("risk") == "high"))
    c4.metric("Schema-valid rate", f"{float(state.get('schema_valid_rate', 1.0)) * 100:.0f}%")
    c5.metric("Est. time saved", f"{len(output) * 1.8:.1f} min")

    if state.get("degraded"):
        st.warning("Degraded mode: some plans used fallback reasoning or could not find a safe slot. Human review required.")

    if state.get("trace"):
        st.subheader("Run trace")
        st.dataframe(pd.DataFrame(state["trace"]), use_container_width=True)

    if state.get("demo_clip") is None and output:
        with st.spinner("Generating working demo clip..."):
            state["demo_clip"] = generate_demo_clip()

    if state.get("demo_clip"):
        st.subheader("Auto-generated demo clip")
        st.image(state["demo_clip"], caption="Working demo clip generated inside the app")
        st.download_button(
            "Download demo clip",
            state["demo_clip"],
            file_name="workflow_demo.gif",
            mime="image/gif",
        )

    st.subheader("Events table")
    df = build_events_table()
    if df.empty:
        st.info("No events yet.")
    else:
        query = st.text_input("Filter events", key="event_filter")
        if query:
            mask = df.astype(str).apply(lambda col: col.str.contains(query, case=False, na=False)).any(axis=1)
            df = df[mask]
        st.dataframe(df, use_container_width=True)
        st.download_button(
            "Download events CSV",
            df.to_csv(index=False).encode("utf-8"),
            file_name="events.csv",
            mime="text/csv",
        )


def render_inbox() -> None:
    state = st.session_state
    emails = get_scenario_emails(state)
    if not emails:
        st.info("No emails in selected scenario.")
        return

    rows = []
    for email in emails:
        received = parse_iso(email.get("received_at")) or utcnow()
        rows.append(
            {
                "ID": email.get("id", ""),
                "From": email.get("sender", ""),
                "Subject": email.get("subject", ""),
                "External": email.get("external", False),
                "Auth": "pass" if email.get("auth_pass", True) else "fail",
                "Received": received.strftime("%Y-%m-%d %H:%M UTC"),
            }
        )

    st.dataframe(pd.DataFrame(rows), use_container_width=True)

    for email in emails:
        with st.expander(f"{email.get('subject', '')} — {email.get('sender', '')}"):
            st.write(email.get("body", ""))
            if not email.get("auth_pass", True):
                st.error("Authentication failure: treat this email as untrusted.")
            if email.get("external"):
                st.warning("External sender: replies and calendar shares are forced to high risk.")


def render_calendar() -> None:
    state = st.session_state
    rows: List[Dict[str, Any]] = []

    def add_row(title: str, start: str, end: str, status: str, source: str, attendees: List[str]) -> None:
        rows.append(
            {
                "Title": title,
                "Start (UTC)": start,
                "End (UTC)": end,
                "Status": status,
                "Source": source,
                "Attendees": ", ".join(attendees or []),
            }
        )

    for event in state.get("calendar", []):
        add_row(
            event.get("title", ""),
            event.get("start", ""),
            event.get("end", ""),
            event.get("status", "confirmed"),
            event.get("source", "calendar"),
            event.get("attendees", []),
        )

    for event in state.get("scheduled_events", []):
        add_row(
            event.get("title", ""),
            event.get("start", ""),
            event.get("end", ""),
            event.get("status", "approved"),
            "approved action",
            event.get("attendees", []),
        )

    for action in state.get("pending_actions", []):
        if action.get("type") in {"create_calendar_event", "update_calendar_event"}:
            payload = action.get("payload", {})
            add_row(
                payload.get("title", "Proposed event"),
                payload.get("start", ""),
                payload.get("end", ""),
                f"{action.get('status', '')} ghost",
                "agent proposal",
                payload.get("attendees", []),
            )

    if not rows:
        st.info("No calendar events.")
        return

    df = pd.DataFrame(rows)
    offset = float(state["settings"].get("user_timezone_offset_hours", -5.0))

    def fmt_utc(value: str) -> str:
        dt = parse_iso(value)
        return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else ""

    def fmt_local(value: str) -> str:
        dt = parse_iso(value)
        if not dt:
            return ""
        return (dt + timedelta(hours=offset)).strftime("%a %Y-%m-%d %H:%M")

    df["Start (local)"] = df["Start (UTC)"].apply(fmt_local)
    df["End (local)"] = df["End (UTC)"].apply(fmt_local)
    df["Start (UTC)"] = df["Start (UTC)"].apply(fmt_utc)
    df["End (UTC)"] = df["End (UTC)"].apply(fmt_utc)

    st.dataframe(
        df[["Title", "Start (local)", "End (local)", "Status", "Source", "Attendees"]],
        use_container_width=True,
    )


def render_actions() -> None:
    state = st.session_state
    st.subheader("Human gate: approve, edit, or reject")
    st.caption("No side effect is performed automatically. Approved actions are simulated and have a 60-second undo window.")

    if not state.get("pending_actions"):
        st.info("Run the simulation to create proposed actions.")
        return

    for action in state["pending_actions"]:
        render_action_card(action)


def render_results() -> None:
    state = st.session_state
    output = build_final_output()

    if not output:
        st.info("No results yet.")
        return

    rows = []
    for result in output:
        rows.append(
            {
                "email_id": result.get("email_id", ""),
                "intent": result.get("intent", ""),
                "tasks": len(result.get("tasks", [])),
                "actions": len(result.get("proposed_actions", [])),
                "conflicts": "; ".join(result.get("conflicts", [])),
                "reasoning": str(result.get("reasoning", ""))[:140],
            }
        )

    st.dataframe(pd.DataFrame(rows), use_container_width=True)

    for result in output:
        with st.expander(f"{result.get('email_id', '')} — {result.get('intent', '')}"):
            st.json(result)

    st.download_button(
        "Download full JSON result",
        safe_json_dumps(output).encode("utf-8"),
        file_name="workflow_output.json",
        mime="application/json",
    )


def render_audit_log() -> None:
    state = st.session_state
    log = state.get("audit_log", [])
    if not log:
        st.info("No audit entries yet.")
        return

    df = pd.DataFrame(log)
    st.dataframe(df, use_container_width=True)
    st.download_button(
        "Download audit CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name="audit_log.csv",
        mime="text/csv",
    )


def render_about() -> None:
    st.subheader("What this demo proves")
    st.markdown(
        """
        This app demonstrates a safe workflow orchestrator pattern:

        - Emails and calendar events are ingested.
        - Tasks and intents are extracted.
        - The planner proposes actions only.
        - Every side-effect action requires explicit human approval.
        - Approved actions are simulated, not executed against real services.
        - A 60-second undo window is attached to approvals.
        - All decisions are written to an audit log.
        """
    )

    st.subheader("What the AI is not allowed to do")
    st.markdown(
        """
        - It cannot transfer funds, buy gift cards, or expose banking details.
        - It cannot send email or update calendar events without approval.
        - It cannot act on authentication-failed or spoofed executive payment requests.
        - It cannot over-share calendar details with external recipients.
        - It cannot bypass the human gate, even in dry-run mode.
        """
    )

    st.subheader("Guardrail self-tests")
    tests = st.session_state.get("guardrail_tests", [])
    if tests:
        st.dataframe(pd.DataFrame(tests), use_container_width=True)
    else:
        st.info("No guardrail tests available.")

    st.subheader("No API key required")
    st.markdown(
        """
        This version uses a deterministic simulated intelligence layer so it can run offline.
        The structure is designed so a real Gemini + Google API layer can replace the simulator
        without changing the human-gate contract.
        """
    )


def main() -> None:
    try:
        init_state()
        refresh_action_statuses()
        render_sidebar()

        st.title("🧭 Workflow Orchestrator Agent")
        st.caption("Email → task/calendar proposals → human approval → auditable scheduled actions and drafts.")

        tabs = st.tabs(["Live Run", "Inbox", "Calendar", "Actions", "Results", "Audit Log", "About"])

        with tabs[0]:
            if st.session_state.get("auto_demo", False):
                try:
                    run_simulation(st.container())
                except Exception as exc:
                    st.error(f"Simulation failed and was safely stopped: {exc}")
                    st.session_state["degraded"] = True
                finally:
                    st.session_state["auto_demo"] = False

            render_live_summary()

        with tabs[1]:
            render_inbox()

        with tabs[2]:
            render_calendar()

        with tabs[3]:
            render_actions()

        with tabs[4]:
            render_results()

        with tabs[5]:
            render_audit_log()

        with tabs[6]:
            render_about()

    except Exception as exc:
        st.error(f"Application error: {exc}")
        st.session_state["degraded"] = True


if __name__ == "__main__":
    main()