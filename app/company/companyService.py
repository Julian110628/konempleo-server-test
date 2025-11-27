
import os
import uuid
import boto3
from fastapi import HTTPException, UploadFile
from db import session
from app.baseController import ControllerBase
from app.company.companyDTO import CompanyCreate, CompanyUpdate, CompanySoftDelete
from cryptography.fernet import Fernet

from models.models import Company


AWS_REGION = os.getenv("AWS_REGION", "us-east-2")
S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET") or os.getenv("BUCKET_NAME")

if not S3_BUCKET_NAME:
    raise RuntimeError("S3 bucket name is not configured. Set AWS_S3_BUCKET or BUCKET_NAME.")

# Usar IAM Role (NO pasar keys manualmente)
s3_client = boto3.client("s3", region_name=AWS_REGION)


class ServiceCompany(ControllerBase[Company, CompanyCreate, CompanyUpdate, CompanySoftDelete]): 
    ...

company = ServiceCompany(Company)

def upload_picture_to_s3(picture: UploadFile, company_name: str) -> str:
    try:
        # Generate a unique file name
        file_extension = picture.filename.split('.')[-1]
        unique_filename = f"{uuid.uuid4()}.{file_extension}"

        # Ensure the company name doesn't contain spaces or invalid characters
        sanitized_company_name = company_name.replace(' ', '_').lower()

        # Construct the S3 key (path) with the folder structure
        s3_key = f"{sanitized_company_name}/logo/{unique_filename}"

        # Upload the file to S3
        s3_client.upload_fileobj(
            picture.file,
            S3_BUCKET_NAME,
            s3_key,
            ExtraArgs={"ContentType": picture.content_type}
        )

        # Generate the URL to the file
        picture_url = f"https://{S3_BUCKET_NAME}.s3.amazonaws.com/{s3_key}"

        return picture_url

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload picture: {str(e)}")
