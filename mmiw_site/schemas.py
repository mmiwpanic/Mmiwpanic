from __future__ import annotations
from pydantic import BaseModel, Field, EmailStr
from typing import Optional, Literal, List
PublicLevel = Literal["public","partners","le_only"]
class CaseIn(BaseModel):
    status: Literal["missing","found","homicide","unsolved"]
    name: str = Field(..., min_length=1)
    dob: Optional[str] = None
    age_at_disappearance: Optional[int] = Field(None, ge=0)
    gender: Optional[str] = None
    tribal_affiliation: Optional[str] = None
    last_seen_date: Optional[str] = None
    last_seen_city: Optional[str] = None
    last_seen_state: Optional[str] = None
    geo_precision: Optional[Literal["block","neighborhood","city","region"]] = "city"
    public_level: PublicLevel = "public"
    family_consent: bool = True
class CaseOut(CaseIn): id: str
class TipIn(BaseModel):
    case_id: Optional[str] = None
    named: bool = False
    contact: Optional[str] = None
    message: str = Field(..., min_length=3)
class TipOut(BaseModel): id: str; case_id: Optional[str]; created_at: int
class LERequestIn(BaseModel):
    agency: str; contact_email: EmailStr; case_id: Optional[str] = None; statutory_basis: Optional[str] = None; scope: str
class LERequestOut(BaseModel): id: str; status: str; created_at: int

class UserCreateIn(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=80)
class UserCreateOut(BaseModel):
    user_id: str
    api_key: str
    warning: str = "Save this API key now. It cannot be shown again. Losing it means losing access to your contacts and safety profile."

ContactType = Literal["email", "sms"]
class ContactIn(BaseModel):
    label: str = Field(..., min_length=1, max_length=60)
    contact_type: ContactType
    destination: str = Field(..., min_length=3)
    is_le: bool = False
    priority: int = Field(1, ge=1, le=5)
class ContactOut(ContactIn):
    id: str
    created_at: int

class SafetyProfileIn(BaseModel):
    full_name: Optional[str] = None
    description: Optional[str] = Field(None, max_length=500)
    vehicle: Optional[str] = Field(None, max_length=200)
    emergency_note: Optional[str] = Field(None, max_length=500)
    home_address: Optional[str] = Field(None, max_length=300)
    tracking_enabled: bool = Field(
        False, description="Pre-authorization: allow location tracking to start automatically on panic trigger."
    )
    checkin_window_sec: int = Field(
        90, ge=15, le=1800,
        description="Seconds after a panic trigger before deliveries auto-send if the user hasn't checked in as safe.",
    )
    location_retention_days: int = Field(
        30, ge=1, le=365,
        description="How many days a location trail is kept before automatic deletion, if not attached to an active case.",
    )
    auto_delete_if_no_case: bool = Field(
        True, description="If true, location trails are deleted after retention expires unless linked to an active case."
    )
class SafetyProfileOut(SafetyProfileIn):
    updated_at: int

class PanicTriggerIn(BaseModel):
    note: Optional[str] = Field(None, max_length=1000)
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lng: Optional[float] = Field(None, ge=-180, le=180)
    include_le: bool = False

class PanicDeliveryStatus(BaseModel):
    contact_label: str
    channel: str
    ok: bool
    error: Optional[str] = None

class PanicTriggerOut(BaseModel):
    panic_event_id: str
    redirect: str
    status: str
    checkin_deadline: Optional[int] = None
    tracking_active: bool = False
    deliveries: List[PanicDeliveryStatus]

class CheckinIn(BaseModel):
    panic_event_id: str

class LocationPingIn(BaseModel):
    panic_event_id: str
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    accuracy_m: Optional[float] = Field(None, ge=0)

class LocationPointOut(BaseModel):
    lat: float
    lng: float
    accuracy_m: Optional[float]
    recorded_at: int

class LocationTrailOut(BaseModel):
    panic_event_id: str
    status: str
    points: List[LocationPointOut]
