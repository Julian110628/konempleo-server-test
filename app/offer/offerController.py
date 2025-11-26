from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from requests import Session
from sqlalchemy import case, func

from app.auth.authDTO import UserToken
from app.auth.authService import get_user_current
from app.deps import get_db
from app.offer.offerDTO import Offer, OfferCreateDTO, OfferUpdateDTO, OfferWithVitaeCount
from models.models import CVitae, Cargo, Company, CompanyOffer, CompanyUser, OfferSkill, Skill, UserEnum, Users, VitaeOffer
from models.models import Offer as OfferModel

offerRouter = APIRouter()
offerRouter.tags = ['Offer']


@offerRouter.post("/offers/", response_model=Offer)
def create_offer(
    offer_in: OfferCreateDTO,
    skills: Optional[List[str]] = None,
    db: Session = Depends(get_db),
    userToken: UserToken = Depends(get_user_current),
):
    """
    Create a new offer and associate it with the provided skills and the offer owner.
    """
    # Ensure that the current user is a company user
    if userToken.role not in [UserEnum.company_recruit, UserEnum.company]:
        raise HTTPException(status_code=403, detail="No tiene los permisos para ejecutar este servicio")
    
    # Check if the company exists
    offerCompanyId = offer_in.companyId
    company = db.query(Company).filter(Company.id == offerCompanyId).first()
    if not company:
        raise HTTPException(status_code=400, detail=f"Invalid company ID: {offer_in.companyId}")
    
    # Check if the cargo exists
    offerCargoId = offer_in.cargoId
    cargo = db.query(Cargo).filter(Cargo.id == offerCargoId).first()
    if not cargo:
        raise HTTPException(status_code=400, detail=f"Invalid cargo ID: {offer_in.companyId}")

    # Check if the current active offers will exceed the total offers
    if company.availableoffers -1 < 0:
        raise HTTPException(status_code=402, detail="Cannot create new offer. There arent any available offers for this company.") 

    # Prepare offer data
    offer_data = offer_in.dict()
    offer_data.pop('companyId', None)  # Remove companyId, we'll handle this later

    try:
        # Create the Offer
        new_offer = OfferModel(**offer_data)
        new_offer.offer_owner = userToken.id  # Associate the user as offer_owner
        db.add(new_offer)
        db.flush()  # Flush to get the new offer's ID

        # Handle skills as names: buscar por nombre o crear si no existe
        skill_ids = []

        if skills:
            for raw in skills:
                name = (raw or "").strip()
                if not name:
                    continue

                # Buscar skill por nombre (case-insensitive)
                skill = (
                    db.query(Skill)
                    .filter(Skill.name.ilike(name))
                    .first()
                )

                # Crear skill si no existe
                if not skill:
                    skill = Skill(name=name)
                    db.add(skill)
                    db.flush()

                skill_ids.append(skill.id)

        # Crear relaciones offer_skill
        for sid in skill_ids:
            db.add(OfferSkill(offerId=new_offer.id, skillId=sid))

        # Associate the offer with the company
        company_offer = CompanyOffer(offerId=new_offer.id, companyId=offerCompanyId)
        db.add(company_offer)

        # Increment the company's activeoffers
        company.activeoffers += 1
        company.availableoffers = company.availableoffers -1
        db.add(company)

        # Commit the transaction to save everything
        db.commit()

        # Refresh the new offer to return updated data
        db.refresh(new_offer)

        return new_offer
    
    except HTTPException as e:
        raise e  # Re-raise the HTTPException for invalid skill or user ID

    except Exception as e:
        db.rollback()  # Rollback in case of errors
        raise HTTPException(status_code=500, detail=f"An error occurred while creating the offer: {str(e)}")



@offerRouter.put("/offers/{offer_id}", response_model=Offer)
def update_offer(
    offer_id: int,
    offer_update: OfferUpdateDTO,
    db: Session = Depends(get_db),
    userToken: UserToken = Depends(get_user_current)
):
    """
    Update specific fields in an offer: assigned_cvs, active status (only from True to False),
    and update the company's activeoffers field using the offerCompany relationship.
    """
    try:
        # Ensure that the current user is a company user
        if userToken.role not in [UserEnum.super_admin, UserEnum.company_recruit, UserEnum.company]:
            raise HTTPException(status_code=403, detail="No tiene los permisos para ejecutar este servicio")

        # Fetch the offer by ID
        offer = db.query(OfferModel).filter(OfferModel.id == offer_id).first()
        if not offer:
            raise HTTPException(status_code=404, detail="Offer not found")

        # Track if the active status changes
        active_status_changed = False

        # Update the allowed fields if they are provided
        if offer_update.assigned_cvs is not None:
            offer.assigned_cvs = offer_update.assigned_cvs

        if offer_update.active is not None:
            # Allow only transition from active=True to active=False
            if offer.active and not offer_update.active:
                active_status_changed = True
                offer.active = False
            elif offer.active != offer_update.active:
                raise HTTPException(
                    status_code=400,
                    detail="The 'active' field can only be updated from True to False."
                )

        # Commit the updates to the offer
        db.commit()

        # Update the company's activeoffers if the active status changed
        if active_status_changed:
            # Fetch the related company directly through the offerCompany table
            company = (
                db.query(Company)
                .join(CompanyOffer, CompanyOffer.companyId == Company.id)
                .filter(
                    CompanyOffer.offerId == offer_id,  # Match the offer
                    Company.is_deleted == False        # Exclude deleted companies
                )
                .first()
            )

            if company:
                company.activeoffers = max(0, company.activeoffers - 1)
                db.commit()

        # Refresh and return the updated offer
        db.refresh(offer)
        return offer

    except Exception as e:
        db.rollback()
        print(f"Error while updating offer ID {offer_id}: {str(e)}")  # Log t


@offerRouter.get("/offers/company/details/{company_id}", response_model=List[OfferWithVitaeCount])
def get_offers_by_company(
    company_id: int,
    start_date: Optional[datetime] = None,
    close_date: Optional[datetime] = None,
    db: Session = Depends(get_db),
    userToken: UserToken = Depends(get_user_current),
):
    """
    Get offers for a given company and count the number of associated VitaeOffer records for each offer.
    Optionally filter by start_date and close_date.
    Additionally, include counts of CVitae with background_check not null,
    and use the contacted and interested fields directly from the Offer model.
    The results are sorted from newest to oldest based on created_date.
    """

    if userToken.role not in [UserEnum.super_admin, UserEnum.admin, UserEnum.company]:
        raise HTTPException(status_code=403, detail="No tiene los permisos para ejecutar este servicio")

    # Check if the company exists
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    # Validate start_date and close_date
    if start_date and close_date:
        if close_date <= start_date:
            raise HTTPException(
                status_code=400, detail="close_date must be greater than start_date"
            )
    elif start_date or close_date:
        raise HTTPException(
            status_code=400, detail="Both start_date and close_date must be provided together"
        )

    # Base query with additional counts for background_check and cargo name
    query = db.query(
        OfferModel,
        Cargo.name.label("cargo_name"),  # Fetch the cargo name
        func.count(VitaeOffer.id).label('vitae_offer_count'),
        func.count(func.nullif(CVitae.background_check, None)).label('background_check_count'),
    ).join(
        CompanyOffer, CompanyOffer.offerId == OfferModel.id
    ).outerjoin(
        VitaeOffer, VitaeOffer.offerId == OfferModel.id
    ).outerjoin(
        CVitae, CVitae.Id == VitaeOffer.cvitaeId
    ).outerjoin(
        Cargo, Cargo.id == OfferModel.cargoId  # Join with Cargo table to get name
    ).filter(
        CompanyOffer.companyId == company_id
    ).group_by(
        OfferModel.id, Cargo.name  # Group by both Offer ID and Cargo Name
    ).order_by(
        OfferModel.created_date.desc()
    )

    # Apply date filters if provided
    if start_date and close_date:
        query = query.filter(
            OfferModel.created_date >= start_date,
            OfferModel.created_date <= close_date
        )

    # Execute query
    offers_with_vitae_count = query.all()

    # Format the response using contacted and interested fields
    result = []
    for offer, cargo_name, vitae_offer_count, background_check_count in offers_with_vitae_count:
        offer_dict = offer.__dict__.copy()  # Convert the offer object to a dictionary
        offer_dict['vitae_offer_count'] = vitae_offer_count
        offer_dict['background_check_count'] = background_check_count
        offer_dict['cargo_name'] = cargo_name  # Add cargo name to the response
        offer_dict['start_date'] = offer.created_date  # Add start_date to response
        offer_dict['close_date'] = offer.modified_date  # Add close_date to response
        result.append(OfferWithVitaeCount(**offer_dict))
    
    return result

@offerRouter.get("/offers/owner/", response_model=List[OfferWithVitaeCount])
def get_offers_by_owner(
    start_date: Optional[datetime] = None,
    close_date: Optional[datetime] = None,
    db: Session = Depends(get_db),
    userToken: UserToken = Depends(get_user_current),
):
    """
    Get offers for a given offer owner and count the number of associated VitaeOffer records for each offer.
    Optionally filter by start_date and close_date.
    Additionally, use the contacted and interested fields directly from the Offer model.
    The results are sorted from newest to oldest based on created_date.
    """

    # Ensure only super_admin or company users can access this
    if userToken.role not in [UserEnum.super_admin, UserEnum.company, UserEnum.company_recruit]:
        raise HTTPException(status_code=403, detail="No tiene los permisos para ejecutar este servicio")

    current_user_id = userToken.id

    # Check if the offer owner exists
    owner = db.query(Users).filter(Users.id == current_user_id).first()
    if not owner:
        raise HTTPException(status_code=404, detail="Offer owner not found")

    # Validate start_date and close_date
    if start_date and close_date:
        if close_date <= start_date:
            raise HTTPException(
                status_code=400, detail="close_date must be greater than start_date"
            )
    elif start_date or close_date:
        raise HTTPException(
            status_code=400, detail="Both start_date and close_date must be provided together"
        )

    # Base query with additional counts for background_check and cargo name
    query = db.query(
        OfferModel,
        Cargo.name.label("cargo_name"),  # Fetch the cargo name
        func.count(VitaeOffer.id).label("vitae_offer_count"),
        func.count(func.nullif(CVitae.background_check, None)).label("background_check_count"),
    ).outerjoin(
        VitaeOffer, VitaeOffer.offerId == OfferModel.id
    ).outerjoin(
        CVitae, CVitae.Id == VitaeOffer.cvitaeId
    ).outerjoin(
        Cargo, Cargo.id == OfferModel.cargoId  # Join with Cargo table to get name
    ).filter(
        OfferModel.offer_owner == current_user_id
    ).group_by(
        OfferModel.id, Cargo.name  # Group by both Offer ID and Cargo Name
    ).order_by(
        OfferModel.created_date.desc()  # Order from newest to oldest
    )

    # Apply date filters if provided
    if start_date and close_date:
        query = query.filter(
            OfferModel.created_date >= start_date,
            OfferModel.created_date <= close_date
        )

    # Execute query
    offers_with_vitae_count = query.all()

    # Format the response using contacted and interested fields
    result = []
    for offer, cargo_name, vitae_offer_count, background_check_count in offers_with_vitae_count:
        offer_dict = offer.__dict__.copy()  # Convert the offer object to a dictionary
        offer_dict["vitae_offer_count"] = vitae_offer_count
        offer_dict["background_check_count"] = background_check_count
        offer_dict["cargo_name"] = cargo_name  # Add cargo name to the response
        offer_dict["start_date"] = offer.created_date  # Add start_date to response
        offer_dict["close_date"] = offer.modified_date  # Add close_date to response
        result.append(OfferWithVitaeCount(**offer_dict))

    return result

@offerRouter.get("/offers/details/{offer_id}", response_model=OfferWithVitaeCount)
def get_offer_by_id(
    offer_id: int, 
    db: Session = Depends(get_db), 
    userToken: UserToken = Depends(get_user_current)
) -> OfferWithVitaeCount:
    """
    Get a specific offer by its ID and count the associated VitaeOffer records.
    Additionally, retrieve the cargo name associated with the offer's cargoId.
    """

    # Check user permissions
    if userToken.role not in [UserEnum.super_admin, UserEnum.company]:
        raise HTTPException(status_code=403, detail="You do not have permission to view this offer.")

    # Query to fetch the offer and count associated VitaeOffer records along with cargo name
    result = db.query(
        OfferModel,
        func.coalesce(Cargo.name, None).label("cargo_name"),  # Fetch cargo name and handle NULLs
        func.count(VitaeOffer.id).label("vitae_offer_count"),
        func.count(func.nullif(CVitae.background_check, None)).label("background_check_count"),  # Add missing field
    ).outerjoin(
        VitaeOffer, VitaeOffer.offerId == OfferModel.id
    ).outerjoin(
        CVitae, CVitae.Id == VitaeOffer.cvitaeId  # Join to count background_check
    ).outerjoin(
        Cargo, Cargo.id == OfferModel.cargoId  # Join with Cargo table to get name
    ).filter(
        OfferModel.id == offer_id
    ).group_by(
        OfferModel.id, Cargo.name  # Group by Offer ID and Cargo Name
    ).first()

    if not result:
        raise HTTPException(status_code=404, detail="Offer not found")

    # Unpack the query result
    offer, cargo_name, vitae_offer_count, background_check_count = result

    # Ensure cargo_name is properly handled
    cargo_name = cargo_name if cargo_name else None  # Explicitly set None if it's an invalid value

    # Prepare the response
    offer_dict = offer.__dict__.copy()  # Convert the offer object to a dictionary
    offer_dict["vitae_offer_count"] = vitae_offer_count
    offer_dict["background_check_count"] = background_check_count  # ✅ Now included
    offer_dict["cargo_name"] = cargo_name  # Add cargo name to the response

    return OfferWithVitaeCount(**offer_dict)