
import json
import os
import traceback
from typing import List, Optional
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func
from app.auth.authDTO import UserToken
from app.auth.authService import generate_presigned_url, get_password_hash, get_user_current
from app.company.companyDTO import Company, CompanyCreate, CompanyInDBBaseWCount, CompanyUpdate, CompanyWCount, CompanyWCountWithRecruiter
from sqlalchemy.orm import Session
from app.company.companyService import upload_picture_to_s3
from app import deps
from app.user.userService import generate_temp_password, send_email_with_temp_password
from models.models import CVitae, CompanyOffer, CompanyUser, Offer, UserEnum, Users
from models.models import Company as CompanyModel

companyRouter = APIRouter()
companyRouter.tags = ['Company']
fields_to_update = [
        "name", "sector", "document", "document_type", "city",
        "employees", "activeoffers", "availableoffers", "totaloffers",
        "is_deleted", "active"]
S3_BUCKET_NAME = os.getenv("BUCKET_NAME")

@companyRouter.post("/company/", status_code=201, response_model=Company)
def create_company(
    *,
    company_in: str = Form(...),
    picture: Optional[UploadFile] = File(None),
    db: Session = Depends(deps.get_db),
    userToken: UserToken = Depends(get_user_current),
) -> dict:
    """
    Crea una empresa y el usuario responsable (rol company).
    - Crea usuario responsable con password temporal.
    - Valida que el konempleo_responsible exista.
    - Crea la empresa.
    - Crea relaciones en CompanyUser.
    - Intenta enviar el mail con la contraseña temporal (NO rompe si falla).
    - Intenta subir logo a S3 (NO rompe si falla).

    Versión "fuerte": agrega contexto de etapa en errores 500 para debug.
    """

    debug_ctx = {"stage": "start"}

    try:
        debug_ctx["stage"] = "parse_company_payload"
        # company_in viene como string en form-data → parseamos a dict
        raw_company = json.loads(company_in)
        company_data = CompanyCreate(**raw_company)

        debug_ctx["stage"] = "auth_check"
        # Solo admin / super_admin pueden crear empresas
        if userToken.role not in [UserEnum.super_admin, UserEnum.admin]:
            raise HTTPException(
                status_code=403,
                detail="No tiene los permisos para ejecutar este servicio",
            )

        # super_admin crea empresas activas, admin las deja inactivas
        active_state = userToken.role == UserEnum.super_admin

        debug_ctx["stage"] = "create_responsible_user"
        # 1) Usuario responsable (rol company)
        temp_password = generate_temp_password()
        hashed_password = get_password_hash(temp_password)

        user = Users(
            fullname=company_data.responsible_user.fullname,
            email=company_data.responsible_user.email,
            password=hashed_password,
            phone=company_data.responsible_user.phone,
            role=UserEnum.company,
        )
        db.add(user)
        db.flush()  # para tener user.id

        debug_ctx["stage"] = "validate_konempleo_responsible"
        # 2) Validar konempleo_responsible
        konempleo_user = (
            db.query(Users)
            .filter(Users.id == company_data.konempleo_responsible)
            .first()
        )
        if not konempleo_user:
            db.rollback()
            raise HTTPException(
                status_code=404,
                detail="Konempleo responsible user not found.",
            )

        if konempleo_user.role not in [UserEnum.admin, UserEnum.super_admin]:
            db.rollback()
            raise HTTPException(
                status_code=400,
                detail="Konempleo responsible must be an admin user.",
            )

        debug_ctx["stage"] = "create_company_model"
        # 3) Preparar data de la empresa
        company_dict = company_data.dict()
        company_dict.pop("konempleo_responsible", None)
        company_dict.pop("responsible_user", None)

        company = CompanyModel(**company_dict)
        company.active = active_state

        db.add(company)
        db.flush()  # company.id

        debug_ctx["stage"] = "create_company_user_relations"
        # 4) Relaciones company-user
        company_user = CompanyUser(companyId=company.id, userId=user.id)
        konempleo_user_relation = CompanyUser(
            companyId=company.id, userId=konempleo_user.id
        )
        db.add(company_user)
        db.add(konempleo_user_relation)

        debug_ctx["stage"] = "commit_before_side_effects"
        # 5) Commit de todo lo anterior (sin side effects externos)
        db.commit()
        db.refresh(company)

        debug_ctx["stage"] = "send_email_with_temp_password"
        # 6) Intentar enviar email (NO rompemos si falla, solo log)
        try:
            send_email_with_temp_password(user.email, temp_password)
        except Exception as email_error:
            # Log fuerte para debug, pero no rompemos la creación
            print(
                f"[WARN] Failed to send temp password email "
                f"to {user.email}: {email_error}"
            )
            traceback.print_exc()

        debug_ctx["stage"] = "upload_picture_to_s3"
        # 7) Intentar subir logo a S3 (NO rompe si falla)
        if picture:
            try:
                picture_url = upload_picture_to_s3(picture, company_data.name)
                company.picture = picture_url
                db.commit()
            except Exception as e:
                print(
                    f"[WARN] Failed to upload picture to S3 for company "
                    f"{company_data.name}: {e}"
                )
                traceback.print_exc()
                # NO hacemos rollback, solo dejamos la empresa sin logo

        debug_ctx["stage"] = "return_company"
        return company

    except HTTPException:
        # Errores controlados se pasan tal cual
        raise
    except Exception as e:
        # Cualquier error inesperado → rollback + detalle con etapa
        db.rollback()
        print(
            f"[ERROR] Unexpected error in create_company at stage "
            f"{debug_ctx.get('stage')}: {e}"
        )
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Error inesperado al crear la compañía "
                   f"(stage={debug_ctx.get('stage')}): {str(e)}",
        )

    
@companyRouter.put("/company/{company_id}", response_model=Company)
def update_company(
    company_id: int,
    company_in: CompanyUpdate = Body(...),
    db: Session = Depends(deps.get_db),
    userToken: UserToken = Depends(get_user_current)
) -> dict:
    """
    Update a company in the database.
    If a new responsible user is created, a temporary password is sent via email.
    """

    # Check if the user has sufficient permissions
    if userToken.role not in [UserEnum.super_admin, UserEnum.admin]:
        raise HTTPException(status_code=403, detail="No tiene los permisos para ejecutar este servicio")

    # Fetch the company by ID
    company = db.query(CompanyModel).filter(CompanyModel.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    # Update company fields
    fields_to_update = [
        'name', 'address', 'phone', 'website', 'description', 'active',
        'availableoffers', 'totaloffers', 'is_deleted'
    ]

    for field in fields_to_update:
        value = getattr(company_in, field, None)
        if value is not None:
            setattr(company, field, value)

    # Variable to store the temp password (if a new user is created)
    temp_password = None

    # Handle responsible_user update (role = company)
    if company_in.responsible_user:
        # Check if the new responsible user already exists
        responsible_user = db.query(Users).filter(Users.email == company_in.responsible_user.email).first()

        # Find the current responsible user for this company
        current_responsible_user_record = db.query(CompanyUser).join(Users).filter(
            CompanyUser.companyId == company_id,
            Users.role == UserEnum.company
        ).first()

        if current_responsible_user_record:
            current_responsible_user = db.query(Users).filter(Users.id == current_responsible_user_record.userId).first()

            # If the new responsible user is different from the current one, deactivate the old one and remove the CompanyUser record
            if current_responsible_user and current_responsible_user.email != company_in.responsible_user.email:
                # Deactivate the old responsible user
                current_responsible_user.active = False
                db.add(current_responsible_user)
                
                # Remove the old CompanyUser record
                db.delete(current_responsible_user_record)
            else:
                current_responsible_user.phone = company_in.responsible_user.phone
                db.add(current_responsible_user)
                db.flush()

        # Create a new responsible user if they don't already exist
        if not responsible_user:
            temp_password = generate_temp_password()
            hashed_password = get_password_hash(temp_password)

            new_user = Users(
                fullname=company_in.responsible_user.fullname,
                email=company_in.responsible_user.email,
                password=hashed_password, 
                phone=company_in.responsible_user.phone,
                role=UserEnum.company
            )
            db.add(new_user)
            db.flush()  # Get the new user ID
            responsible_user = new_user

        # Check if a CompanyUser record already exists
        existing_company_user = db.query(CompanyUser).filter(
            CompanyUser.companyId == company_id,
            CompanyUser.userId == responsible_user.id
        ).first()
        if not existing_company_user:
            # Add a new CompanyUser record for the new responsible user
            new_company_user = CompanyUser(companyId=company_id, userId=responsible_user.id)
            db.add(new_company_user)

    # Handle konempleo_responsible update (role = admin)
    if company_in.konempleo_responsible:
        admin_user = db.query(Users).filter(Users.id == company_in.konempleo_responsible).first()
        if not admin_user or admin_user.role != UserEnum.admin:
            raise HTTPException(status_code=400, detail="Invalid admin user")

        # Find the current konempleo_responsible record
        current_konempleo_record = db.query(CompanyUser).join(Users).filter(
            CompanyUser.companyId == company_id,
            Users.role == UserEnum.admin
        ).first()

        # Remove the old CompanyUser record if it's different from the new one
        if current_konempleo_record and current_konempleo_record.userId != company_in.konempleo_responsible:
            db.delete(current_konempleo_record)

        # Check if a CompanyUser record already exists
        existing_konempleo_user = db.query(CompanyUser).filter(
            CompanyUser.companyId == company_id,
            CompanyUser.userId == admin_user.id
        ).first()
        if not existing_konempleo_user:
            # Add a new CompanyUser record for the new konempleo_responsible
            new_konempleo_user = CompanyUser(companyId=company_id, userId=admin_user.id)
            db.add(new_konempleo_user)

    # Commit the changes
    db.commit()
    db.refresh(company)

    # Send the temporary password email if a new responsible user was created
    if temp_password:
        send_email_with_temp_password(responsible_user.email, temp_password)

    return company


@companyRouter.get("/company/owned/", status_code=200, response_model=List[CompanyWCountWithRecruiter])
def get_company(
    *, db: Session = Depends(deps.get_db), userToken: UserToken = Depends(get_user_current)
) -> List[CompanyWCountWithRecruiter]:
    """
    Gets companies owned by the user in the database along with recruiter info.
    Only includes companies that are not marked as deleted (is_deleted = False).
    """

    # Step 1: Get company IDs owned by the user
    company_user_records = db.query(CompanyUser).join(CompanyModel).filter(
        CompanyUser.userId == userToken.id,
        CompanyModel.is_deleted == False  # Filter out deleted companies
    ).all()

    if not company_user_records:
        raise HTTPException(status_code=404, detail="No companies found for the given user ID.")

    company_ids = [record.companyId for record in company_user_records]

    # Step 2: Subquery for recruiter information
    recruiter_subquery = db.query(
        CompanyUser.companyId.label("company_id"),
        Users.fullname.label("recruiter_name"),
        Users.email.label("recruiter_email"),
        Users.phone.label("recruiter_phone"),
        func.row_number().over(
            partition_by=CompanyUser.companyId,
            order_by=Users.id
        ).label("row_number")
    ).join(
        Users, Users.id == CompanyUser.userId
    ).filter(
        Users.role == UserEnum.company,
        Users.active == True,
        Users.is_deleted == False  # Exclude deleted recruiters
    ).subquery()

    # Step 3: Subquery for CV counts
    cv_count_subquery = db.query(
        CVitae.companyId.label("company_id"),
        func.count(CVitae.Id).label("cv_count")
    ).group_by(
        CVitae.companyId
    ).subquery()

    # Step 4: Subquery for contacted and interested totals
    offer_totals_subquery = db.query(
        CompanyOffer.companyId.label("company_id"),
        func.sum(func.coalesce(Offer.contacted, 0)).label("total_contacted"),
        func.sum(func.coalesce(Offer.interested, 0)).label("total_interested")
    ).join(
        Offer, (Offer.id == CompanyOffer.offerId) & (Offer.active == True)
    ).group_by(
        CompanyOffer.companyId
    ).subquery()

    # Step 5: Main query to get companies with all aggregated data
    companies_with_details = db.query(
        CompanyModel,
        func.coalesce(cv_count_subquery.c.cv_count, 0).label("cv_count"),
        recruiter_subquery.c.recruiter_name,
        recruiter_subquery.c.recruiter_email,
        recruiter_subquery.c.recruiter_phone,
        func.coalesce(offer_totals_subquery.c.total_contacted, 0).label("total_contacted"),
        func.coalesce(offer_totals_subquery.c.total_interested, 0).label("total_interested")
    ).outerjoin(
        cv_count_subquery, cv_count_subquery.c.company_id == CompanyModel.id
    ).outerjoin(
        recruiter_subquery, (recruiter_subquery.c.company_id == CompanyModel.id) & 
                            (recruiter_subquery.c.row_number == 1)
    ).outerjoin(
        offer_totals_subquery, offer_totals_subquery.c.company_id == CompanyModel.id
    ).filter(
        CompanyModel.id.in_(company_ids),
        CompanyModel.is_deleted == False  # Exclude deleted companies
    ).all()

    if not companies_with_details:
        return []

    # Step 6: Format the response
    result = []
    for company, cv_count, recruiter_name, recruiter_email, recruiter_phone, total_contacted, total_interested in companies_with_details:
        # Generate a pre-signed URL for the picture
        presigned_url = None
        if company.picture:
            object_key = company.picture.replace(f"https://{S3_BUCKET_NAME}.s3.amazonaws.com/", "")
            presigned_url = generate_presigned_url(object_key)

        result.append(CompanyWCountWithRecruiter(
            id=company.id,
            name=company.name,
            sector=company.sector,
            document=company.document,
            document_type=company.document_type,
            city=company.city,
            picture=presigned_url,  # Pre-signed URL for the picture
            activeoffers=company.activeoffers,
            availableoffers=company.availableoffers,
            totaloffers=company.totaloffers,
            active=company.active,
            is_deleted=company.is_deleted,
            employees=company.employees,
            cv_count=cv_count,
            recruiter_name=recruiter_name,
            recruiter_email=recruiter_email,
            recruiter_phone=recruiter_phone,
            total_contacted=total_contacted,
            total_interested=total_interested
        ))

    return result


@companyRouter.get("/company/all/", status_code=200, response_model=List[CompanyWCount])
def get_all_companies(
    *, db: Session = Depends(deps.get_db), userToken: UserToken = Depends(get_user_current)
) -> List[CompanyWCount]:
    """
    Gets all companies in the database if the user is a super admin.
    Includes the CV count, sums the contacted and interested fields for all offers related to each company,
    and includes the first active admin and recruiter for each company.
    """
    if userToken.role != UserEnum.super_admin:
        raise HTTPException(status_code=403, detail="You do not have permission to view all companies.")

    # Step 1: Subquery to count CVs per company
    cv_count_subquery = db.query(
        CVitae.companyId.label("company_id"),
        func.count(CVitae.Id).label("cv_count")
    ).group_by(
        CVitae.companyId
    ).subquery()

    # Step 2: Subquery to sum contacted and interested fields per company
    offer_totals_subquery = db.query(
        CompanyOffer.companyId.label("company_id"),
        func.sum(func.coalesce(Offer.contacted, 0)).label("total_contacted"),
        func.sum(func.coalesce(Offer.interested, 0)).label("total_interested")
    ).join(
        Offer, (Offer.id == CompanyOffer.offerId) & (Offer.active == True)
    ).group_by(
        CompanyOffer.companyId
    ).subquery()

    # Step 3: Subquery for the first admin (responsible) for each company
    admin_subquery = db.query(
        CompanyUser.companyId.label("company_id"),
        Users.fullname.label("admin_name"),
        Users.email.label("admin_email"),
        func.row_number().over(
            partition_by=CompanyUser.companyId,
            order_by=Users.id
        ).label("row_number")
    ).join(
        Users, Users.id == CompanyUser.userId
    ).filter(
        Users.role == UserEnum.admin,
        Users.active == True,
        Users.is_deleted == False  # Exclude deleted admins
    ).subquery()

    # Step 4: Subquery for the first recruiter for each company
    recruiter_subquery = db.query(
        CompanyUser.companyId.label("company_id"),
        Users.fullname.label("recruiter_name"),
        Users.email.label("recruiter_email"),
        Users.phone.label("recruiter_phone"),
        func.row_number().over(
            partition_by=CompanyUser.companyId,
            order_by=Users.id
        ).label("row_number")
    ).join(
        Users, Users.id == CompanyUser.userId
    ).filter(
        Users.role == UserEnum.company,
        Users.active == True,
        Users.is_deleted == False  # Exclude deleted recruiters
    ).subquery()

    # Step 5: Main query to get companies with aggregated results and responsible data
    companies_query = db.query(
        CompanyModel,
        func.coalesce(cv_count_subquery.c.cv_count, 0).label("cv_count"),
        func.coalesce(offer_totals_subquery.c.total_contacted, 0).label("total_contacted"),
        func.coalesce(offer_totals_subquery.c.total_interested, 0).label("total_interested"),
        admin_subquery.c.admin_name,
        admin_subquery.c.admin_email,
        recruiter_subquery.c.recruiter_name,
        recruiter_subquery.c.recruiter_email,
        recruiter_subquery.c.recruiter_phone
    ).outerjoin(
        cv_count_subquery, cv_count_subquery.c.company_id == CompanyModel.id
    ).outerjoin(
        offer_totals_subquery, offer_totals_subquery.c.company_id == CompanyModel.id
    ).outerjoin(
        admin_subquery, (admin_subquery.c.company_id == CompanyModel.id) & (admin_subquery.c.row_number == 1)
    ).outerjoin(
        recruiter_subquery, (recruiter_subquery.c.company_id == CompanyModel.id) & (recruiter_subquery.c.row_number == 1)
    ).filter(
        CompanyModel.is_deleted == False  # Exclude deleted companies
    ).all()

    # Step 6: Format the response
    result = []
    for company, cv_count, total_contacted, total_interested, admin_name, admin_email, recruiter_name, recruiter_email, recruiter_phone in companies_query:
        # Generate a pre-signed URL for the picture
        presigned_url = None
        if company.picture:
            object_key = company.picture.replace(f"https://{S3_BUCKET_NAME}.s3.amazonaws.com/", "")
            presigned_url = generate_presigned_url(object_key)

        # Build the response object
        result.append(CompanyWCount(
            id=company.id,
            name=company.name,
            sector=company.sector,
            document=company.document,
            document_type=company.document_type,
            city=company.city,
            picture=presigned_url,  # Pre-signed URL for the picture
            activeoffers=company.activeoffers,
            availableoffers=company.availableoffers,
            totaloffers=company.totaloffers,
            active=company.active,
            is_deleted=company.is_deleted,
            employees=company.employees,
            cv_count=cv_count,
            total_contacted=total_contacted,
            total_interested=total_interested,
            admin_name=admin_name,
            admin_email=admin_email,
            recruiter_name=recruiter_name,
            recruiter_email=recruiter_email,
            recruiter_phone=recruiter_phone
        ))

    return result


@companyRouter.get("/company/{company_id}", status_code=200, response_model=CompanyInDBBaseWCount)
def get_company_by_id(
    company_id: int,
    db: Session = Depends(deps.get_db),
    userToken: UserToken = Depends(get_user_current)
) -> CompanyInDBBaseWCount:
    """
    Get a specific company by its ID along with admin info, recruiter info, CV count,
    total_contacted, and total_interested.
    Only includes companies that are not marked as deleted (is_deleted = False).
    """

    # Subquery for admin information
    admin_subquery = db.query(
        CompanyUser.companyId.label("company_id"),
        Users.fullname.label("admin_name"),
        Users.email.label("admin_email"),
        func.row_number().over(
            partition_by=CompanyUser.companyId,
            order_by=Users.id
        ).label("row_number")
    ).join(
        Users, Users.id == CompanyUser.userId
    ).filter(
        Users.role == UserEnum.admin,
        Users.active == True,
        Users.is_deleted == False  # Exclude deleted admin users
    ).subquery()

    # Subquery for recruiter information
    recruiter_subquery = db.query(
        CompanyUser.companyId.label("company_id"),
        Users.fullname.label("recruiter_name"),
        Users.email.label("recruiter_email"),
        Users.phone.label("recruiter_phone"),
        func.row_number().over(
            partition_by=CompanyUser.companyId,
            order_by=Users.id
        ).label("row_number")
    ).join(
        Users, Users.id == CompanyUser.userId
    ).filter(
        Users.role == UserEnum.company,
        Users.active == True,
        Users.is_deleted == False  # Exclude deleted recruiters
    ).subquery()

    # Subquery to calculate contacted and interested totals
    offer_totals_subquery = db.query(
        CompanyOffer.companyId.label("company_id"),
        func.sum(func.coalesce(Offer.contacted, 0)).label("total_contacted"),
        func.sum(func.coalesce(Offer.interested, 0)).label("total_interested")
    ).join(
        Offer, (Offer.id == CompanyOffer.offerId) & (Offer.active == True)
    ).filter(
        CompanyOffer.companyId == company_id
    ).group_by(
        CompanyOffer.companyId
    ).subquery()

    # Main query to fetch company details
    company_with_details = db.query(
        CompanyModel,
        func.count(CVitae.Id).label('cv_count'),
        admin_subquery.c.admin_name,
        admin_subquery.c.admin_email,
        recruiter_subquery.c.recruiter_name,
        recruiter_subquery.c.recruiter_email,
        recruiter_subquery.c.recruiter_phone,
        func.coalesce(offer_totals_subquery.c.total_contacted, 0).label('total_contacted'),
        func.coalesce(offer_totals_subquery.c.total_interested, 0).label('total_interested')
    ).outerjoin(
        CVitae, CVitae.companyId == CompanyModel.id
    ).outerjoin(
        admin_subquery, (admin_subquery.c.company_id == CompanyModel.id) & 
                        (admin_subquery.c.row_number == 1)
    ).outerjoin(
        recruiter_subquery, (recruiter_subquery.c.company_id == CompanyModel.id) & 
                            (recruiter_subquery.c.row_number == 1)
    ).outerjoin(
        offer_totals_subquery, offer_totals_subquery.c.company_id == CompanyModel.id
    ).filter(
        CompanyModel.id == company_id,
        CompanyModel.is_deleted == False  # Exclude deleted companies
    ).group_by(
        CompanyModel.id,
        admin_subquery.c.admin_name, admin_subquery.c.admin_email,
        recruiter_subquery.c.recruiter_name, recruiter_subquery.c.recruiter_email,
        recruiter_subquery.c.recruiter_phone,
        offer_totals_subquery.c.total_contacted,
        offer_totals_subquery.c.total_interested
    ).first()

    if not company_with_details:
        raise HTTPException(status_code=404, detail="Company not found.")

    # Extract company details
    company, cv_count, admin_name, admin_email, recruiter_name, recruiter_email, recruiter_phone, total_contacted, total_interested = company_with_details

    # Generate a pre-signed URL for the picture
    presigned_url = None
    if company.picture:
        object_key = company.picture.replace(f"https://{S3_BUCKET_NAME}.s3.amazonaws.com/", "")
        presigned_url = generate_presigned_url(object_key)

    # Format the response
    return CompanyInDBBaseWCount(
        id=company.id,
        name=company.name,
        sector=company.sector,
        document=company.document,
        document_type=company.document_type,
        city=company.city,
        picture=presigned_url,  # Use the pre-signed URL
        activeoffers=company.activeoffers,
        availableoffers=company.availableoffers,
        totaloffers=company.totaloffers,
        active=company.active,
        is_deleted=company.is_deleted,
        employees=company.employees,
        cv_count=cv_count,
        admin_name=admin_name,
        admin_email=admin_email,
        recruiter_name=recruiter_name,
        recruiter_email=recruiter_email,
        recruiter_phone=recruiter_phone,
        total_contacted=total_contacted,
        total_interested=total_interested
    )
