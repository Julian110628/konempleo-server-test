
from datetime import datetime, timedelta
import os
from typing import  Optional
from aiohttp import ClientError
import boto3
from botocore.config import Config
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from app.deps import get_db
from app.auth.authDTO import TokenData, UserToken
from sqlalchemy.orm import Session
from jose import jwt, JWTError

from models.models import Users

oauth2_scheme = OAuth2PasswordBearer("/login")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def authenticate_user(db: Session, email: str, password: str):
     user = getUserByEmail(db=db, email= email)
     if not user:
          raise HTTPException(status_code=401, detail="Could not validate credentials", headers={"WWW-Authenticate":"Bearer"})
     if not verify_password(password, user.password):
          raise HTTPException(status_code=401, detail="Could not validate credentials", headers={"WWW-Authenticate":"Bearer"})
     if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="You must change your password before continuing."
        )
     return user

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def getUserByEmail(db: Session, email: str) -> Optional[Users]:
        return db.query(Users).filter(Users.email == email).first()

def create_token(data: dict, expires_delta: Optional[datetime]):
    data_copy = data.copy()
    if expires_delta is None:
         expires = datetime.utcnow() + timedelta(minutes=15)
    else:
         expires = datetime.utcnow() + expires_delta
    data_copy.update({"exp":expires})
    token_jwt = jwt.encode(data_copy, key=os.getenv('SECRET_KEY'), algorithm=os.getenv('ALGORITHM'))
    return token_jwt

def generate_token(db: Session, username:str, password: str):
    user = authenticate_user(db, username, password)
    if not user:
        raise HTTPException(status_code=401, detail="Could not validate credentials", headers={"WWW-Authenticate":"Bearer"})
    access_token_expires = timedelta(minutes=int(os.getenv('ACCESS_TOKEN_EXPIRE_MINUTES')))
    payload = {}
    payload["sub"] = user.email
    payload["fullname"] = user.fullname 
    payload["role"] =  user.role
    payload["id"] = user.id
    return create_token(
        data=payload, expires_delta=access_token_expires
    )

def get_user_current(db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)):
     try:
         token_decoded = jwt.decode(token, os.getenv('SECRET_KEY'), algorithms=[os.getenv('ALGORITHM')])
         username = token_decoded.get("sub")
         if username == None:
              raise HTTPException(status_code=401, detail="Could not validate credentials", headers={"WWW-Authenticate":"Bearer"})
         token_data = TokenData(username=username)
     except JWTError:
        raise HTTPException(status_code=401, detail="Could not validate credentials", headers={"WWW-Authenticate":"Bearer"})
     user = getUserByEmail(db=db, email= token_data.username)
     if user is None:
         raise HTTPException(status_code=401, detail="Could not validate credentials", headers={"WWW-Authenticate":"Bearer"})
     user_data_token = UserToken(**{'email':username, 'fullname': token_decoded.get("fullname"), 
                                                    'role':token_decoded.get("role"),
                                                    'id':token_decoded.get("id")})
     return user_data_token
     
def get_password_hash(password):
    return pwd_context.hash(password)


AWS_REGION = os.getenv("AWS_REGION", "us-east-2")
S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET") or os.getenv("BUCKET_NAME")

if not S3_BUCKET_NAME:
    raise RuntimeError("S3 bucket name is not configured. Set AWS_S3_BUCKET or BUCKET_NAME.")

# Use IAM role / environment-provided credentials (do not pass keys explicitly)
s3_client = boto3.client("s3", region_name=AWS_REGION)

def generate_presigned_url(object_key: str, expiration: int = 3600) -> str:
    # Infer the file type from the object key
    file_extension = object_key.split('.')[-1].lower()

    # Determine appropriate headers based on the file type
    content_disposition = 'inline'
    content_type = None  # Default to no explicit content type

    if file_extension == 'pdf':
        content_type = 'application/pdf'
    elif file_extension in ['jpg', 'jpeg', 'png', 'gif']:
        content_type = f'image/{file_extension if file_extension != "jpg" else "jpeg"}'

    try:
        # Set dynamic parameters based on file type
        params = {
            'Bucket': S3_BUCKET_NAME,
            'Key': object_key,
            'ResponseContentDisposition': content_disposition,
        }
        if content_type:
            params['ResponseContentType'] = content_type

        # Generate the pre-signed URL
        response = s3_client.generate_presigned_url(
            'get_object',
            Params=params,
            ExpiresIn=expiration
        )
        return response
    except ClientError as e:
        raise Exception(f"Failed to generate pre-signed URL: {e}")

