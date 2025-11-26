from typing import List, Optional
from pydantic import BaseModel
from enum import IntEnum
from datetime import datetime


from models.models import contractEnum, ShiftEnum, genderEnum, militaryEnum, EducationEnum


class OfferBase(BaseModel):
    name: str
    duties: Optional[str] = None
    exp_area: Optional[str] = None
    vacants: Optional[int] = None
    contract_type: Optional[contractEnum] = None
    salary: Optional[str] = None
    city: Optional[str] = None
    shift: Optional[ShiftEnum] = None
    gender: Optional[genderEnum] = None
    military_notebook: Optional[militaryEnum] = None
    age: Optional[str] = None
    job_type: Optional[str] = None
    license: Optional[List[str]] = ["No Aplica"]
    disabled: Optional[bool] = False
    experience_years: Optional[int] = None
    offer_type: Optional[str] = None
    ed_required: Optional[EducationEnum] = None
    cargoId: Optional[int] = None
    filter_questions: Optional[str] = None
    assigned_cvs: Optional[int] = 0
    active: Optional[bool] = True
    contacted: Optional[int] = 0
    interested: Optional[int] = 0  

class Config:
        use_enum_values = True  # Ensures enums are treated as their values (integers)

# DTO for creating an Offer
class OfferCreateDTO(OfferBase):
    companyId: int
    # ahora aceptamos nombres de skills, no IDs
    skills: Optional[List[str]] = None

class OfferWithVitaeCount(OfferBase):
    id: int
    vitae_offer_count: int

class OfferWithVitaeCount(OfferBase):
    id: int
    vitae_offer_count: int
    background_check_count: int
    cargo_name: Optional[str] = None
    start_date: Optional[datetime] = None
    close_date: Optional[datetime] = None

# DTO for updating an Offer
class OfferUpdateDTO(BaseModel):
    assigned_cvs: Optional[int] = None
    active: Optional[bool] = None

class OfferSoftDelete(BaseModel):
    active: bool

# Properties shared by models stored in DB
class OfferInDBBase(OfferBase):
    id: int

    class Config:
        orm_mode = True

# Properties to return to client
class Offer(OfferInDBBase):
    pass

# Properties stored in DB
class OfferInDB(OfferInDBBase):
    pass
