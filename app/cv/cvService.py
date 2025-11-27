from datetime import datetime
from io import BytesIO
import json
import os
import re
import time
from typing import List
from aiohttp import ClientError
from docx import Document
from fastapi import UploadFile, HTTPException
import openai
from openai import OpenAIError
from pdf2image import convert_from_bytes
import pytesseract
from requests import Session
import boto3
import fitz
import requests
from requests.auth import HTTPBasicAuth
import traceback
from app.utils.prompt import prompt

from models.models import CVitae, VitaeOffer

pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"

# s3_client = boto3.client('s3', aws_access_key_id='your_access_key', aws_secret_access_key='your_secret_key', region_name='your_region')

AWS_REGION = os.getenv("AWS_REGION", "us-east-2")
S3_BUCKET_NAME = os.getenv("AWS_S3_BUCKET") or os.getenv("BUCKET_NAME")
if not S3_BUCKET_NAME:
    raise RuntimeError("S3 bucket name is not configured. Set AWS_S3_BUCKET or BUCKET_NAME.")

# Usar IAM role (sin keys)
s3_client = boto3.client("s3", region_name=AWS_REGION)

openai.api_key =  os.getenv("OAI_KEY")

def upload_to_s3(file: UploadFile, s3_key: str) -> str:
    """
    Upload a file to S3 and return its URL.
    """
    try:
        # Reset the file pointer before uploading
        file.file.seek(0)

        # Upload file to S3
        s3_client.upload_fileobj(file.file, S3_BUCKET_NAME, s3_key)

        # Construct and return the S3 URL
        s3_url = f"https://{S3_BUCKET_NAME}.s3.amazonaws.com/{s3_key}"
        return s3_url
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload file to S3: {str(e)}")

# Helper function to delete a file from S3
def delete_from_s3(url: str):
    """
    Delete a file from S3 using its URL.
    """
    try:
        # Extract bucket name and key from the URL
        match = re.match(r"https://(.+?).s3.amazonaws.com/(.+)", url)
        if not match:
            print(f"Invalid S3 URL: {url}")
            return
        bucket_name, key = match.groups()

        # Initialize S3 client
        s3_client = boto3.client('s3')
        s3_client.delete_object(Bucket=bucket_name, Key=key)
        print(f"Deleted file from S3: {url}")
    except ClientError as e:
        print(f"Failed to delete file from S3: {str(e)}")


def extract_text_from_pdf(file_content: bytes) -> str:
    try:
        # First, try to extract text using PyMuPDF
        with fitz.open(stream=file_content, filetype="pdf") as pdf_doc:
            text = ""
            for page in pdf_doc:
                page_text = page.get_text("text")
                if page_text.strip():  # If text is found, assume it's not an image
                    text += page_text

            # If text was found, return it
            if text.strip():
                return text

        # If no text, assume the PDF has images, so use OCR
        # Convert PDF pages to images
        images = convert_from_bytes(file_content)
        ocr_text = ""

        for image in images:
            try:
                # Use Tesseract for OCR
                ocr_result = pytesseract.image_to_string(image, lang='eng')  # Adjust lang as needed
                ocr_text += ocr_result + "\n"
            finally:
                # Ensure image resources are freed
                image.close()

        return ocr_text.strip()  # Return the OCR text

    except Exception as e:
        return f"An error occurred: {str(e)}"

def extract_text_from_docx(file_content: bytes) -> str:
    doc = Document(BytesIO(file_content))
    text = "\n".join([para.text for para in doc.paragraphs])
    return text

def clean_symbols(text):
  return re.sub(r'[^a-záéíóúA-Z0-9\s]', '', text)


def upload_batch(
    batch: List[dict],
    company_name: str,  # Add company_name as a parameter
):
    urls = {}
    try:
        # Upload files to S3
        for file in batch:
            file_name = file["name"]
            # Upload file to S3
            s3_key = f"{company_name}/cvs/{file_name}"
            urls[file_name] = upload_to_s3(file["file"], s3_key)
    except Exception as e:
        print(f"Error processing batch: {str(e)}")
        raise e
    return urls


def process_file_text(
    batch: List[dict],
    companyId: int,
    company_name: str,  # Add company_name as a parameter
    db: Session,
    urls: dict,
        skills_list, city_offer, age_offer, genre_offer, experience_offer,
        offerId):
    cv_texts = []
    temp_cvitae_records = []
    try:
        # Extract text from each CV, and save temporary CVitae records
        for file in batch:
            file_extension = file["extension"]
            file_name = file["name"]

            # Read the file content and reset pointer
            file_content = file["content"]

            # Extract text based on file type
            if file_extension == 'pdf':
                cv_text = extract_text_from_pdf(file_content)
            elif file_extension in ['docx', 'doc']:
                cv_text = extract_text_from_docx(file_content)
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported file format: {file_extension}")

            cv_texts.append(f"### Candidate #{len(cv_texts) + 1} ###\n{cv_text}")

            # Create a temporary CVitae record with the S3 URL
            temp_cvitae = CVitae(
                url=urls[file_name],
                companyId=companyId,
                extension=file_extension,
                cvtext=cv_text,
            )
            temp_cvitae_records.append(temp_cvitae)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error processing batch: {str(e)}")
        raise e
    try:
        analyze_and_update_vitae_offers(cv_texts, skills_list, city_offer,
                                        age_offer, genre_offer,
                                        experience_offer, db, offerId,
                                        temp_cvitae_records)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error processing texts: {str(e)}")


def parse_prompt(
        cv_texts: List[str],
        skills_list: List[str],
        city_offer: str,
        age_offer: str,
        genre_offer: str,
        experience_offer: int,
):
    skills_list_str = ", ".join(skills_list)
    full_prompt = prompt.format(city_offer=city_offer, age_offer=age_offer,
                                genre_offer=genre_offer,
                                experience_offer=experience_offer,
                                skills_list_str=skills_list_str,
                                cv_texts=cv_texts)

    # Send the request to GPT
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": full_prompt}
    ]
    raw_response = None
    response_json = None

    try:
        def try_to_query(messages):
            global raw_response, response_json
            response = openai.chat.completions.create(
                model="gpt-4-turbo",
                messages=messages,
                temperature=0
            )
            raw_response = response.choices[0].message.content.strip()
            response_json = extract_json(raw_response)
            return response_json

        response_json = try_to_query(messages)
    except openai.RateLimitError:
        print("RateLimit hit. Retrying in 5 seconds...")
        time.sleep(5)
        response_json = try_to_query(messages)
    except OpenAIError as e:
        print(f"Invalid Request Error: {e}")
        print(f"Response Body: {e.response.json()}")  # Log the body
    except json.JSONDecodeError:
        print("JSON Decoding error. Retrying in 5 seconds...")
        time.sleep(5)
        response_json = try_to_query(messages)
    except Exception as e:
        print(f"Unhandled Error: {e}")

    return response_json


def analyze_and_update_vitae_offers(
    cv_texts: List[str],
    skills_list: List[str],
    city_offer: str,
    age_offer: str,
    genre_offer: str,
    experience_offer: int,
    db: Session,
    offerId: int,
    cvitae_records: List[CVitae]
):
    try:
        response_json = parse_prompt(cv_texts, skills_list,
                                     city_offer, age_offer,
                                     genre_offer, experience_offer)
        if response_json is None:
            print("Error processing the OpenAI response")
            raise Exception("Error processing the OpenAI response")

        candidates = response_json.get("candidatos", [])
        valid_cvitae = []

        # Process each candidate
        for idx, (temp_cvitae, candidate_data) in enumerate(zip(cvitae_records, candidates)):
            # Use fallback values for missing fields
            candidate_name = candidate_data.get("nombre", "name not found")

            candidate_email = candidate_data.get("correo", "email not found")

            # Create CVitae record with valid data
            temp_cvitae.candidate_name = candidate_name
            temp_cvitae.candidate_mail = candidate_email
            temp_cvitae.candidate_dni = candidate_data.get("cedula")
            temp_cvitae.candidate_dni_type = candidate_data.get("tipo_documento")
            temp_cvitae.candidate_city = candidate_data.get("ciudad")
            temp_cvitae.candidate_phone = candidate_data.get("movil")

            db.add(temp_cvitae)
            db.flush()  # Save the record to get an ID

            raw_score = candidate_data.get("score")
            try:
                score = float(raw_score)
            except (ValueError, TypeError):
                score = 0.0


            # Create VitaeOffer record
            vitae_offer = VitaeOffer(
                cvitaeId=temp_cvitae.Id,
                offerId=offerId,
                status="pending",
                ai_response=json.dumps(candidate_data),
                response_score=score,
            )
            db.add(vitae_offer)

            valid_cvitae.append(temp_cvitae)

        db.commit()

    except Exception as e:
        # Rollback changes and delete S3 files for all temporary CVitae records
        db.rollback()
        print(f"Error analyzing and creating CVitae/VitaeOffer records: {str(e)}")
        for temp_cvitae in cvitae_records:
            delete_from_s3(temp_cvitae.url)  # Delete the file from S3
        raise HTTPException(status_code=500, detail="An error occurred while analyzing and creating records.")


def fetch_background_check_result(job_id: str, cvitae_id: int, db: Session, retry_interval: int = 10, max_retries: int = 10):
    """
    Background task to fetch the background check result every `retry_interval` seconds
    until a definitive status is returned or the `max_retries` is reached.
    """
    url_get = f"https://dash-board.tusdatos.co/api/results/{job_id}"
    tusDatosUser = os.getenv("tusDatosUser")
    tusDatosSecret = os.getenv("tusDatosSecret")

    for attempt in range(max_retries):
        try:
            # Make the GET request to fetch the background check result
            response_get = requests.get(url_get, auth=HTTPBasicAuth(tusDatosUser, tusDatosSecret))
            response_get.raise_for_status()
            result_data = response_get.json()
        except requests.RequestException as e:
            print(f"Error fetching result for job ID {job_id}: {str(e)}")
            return

        status = result_data.get("estado")
        hallazgo = result_data.get("hallazgo")  # Should be true or false from service

        # Use a new session for each update
        with db.begin():  # Ensure each operation uses a new transaction
            cvitae = db.query(CVitae).filter(CVitae.Id == cvitae_id).first()
            if cvitae:
                # Update background_check only if hallazgo is not None
                if hallazgo is not None:
                    cvitae.background_check = str(hallazgo).lower()  # Save "true" or "false"
                    cvitae.background_date = datetime.utcnow()
                    print(f"Background check completed for CVitae ID {cvitae_id}. Final Status: {status}, Hallazgo: {hallazgo}")
                    return  # Stop retrying after getting a definitive result
                elif status == "procesando":
                    # Temporary status for "procesando"
                    cvitae.background_check = "No findings"
                    cvitae.background_date = datetime.utcnow()
                else:
                    # Handle unexpected states where hallazgo is None, and status is final
                    cvitae.background_check = "Error in results"
                    cvitae.background_date = datetime.utcnow()
                    print(f"Unexpected state for CVitae ID {cvitae_id}. Status: {status}, Hallazgo: {hallazgo}")
                    return

        print(f"Attempt {attempt + 1}: Status for CVitae ID {cvitae_id}: {status}, Hallazgo: {hallazgo}")

        # If the status is not "procesando", stop retrying even if hallazgo is None
        if status != "procesando":
            print(f"Final status reached for CVitae ID {cvitae_id} but no definitive hallazgo. Status: {status}")
            return

        time.sleep(retry_interval)
    # If max retries are reached
    print(f"Max retries reached for job ID {job_id}. Status for CVitae ID {cvitae_id} remains incomplete.")
    with db.begin():
        cvitae = db.query(CVitae).filter(CVitae.Id == cvitae_id).first()
        if cvitae:
            cvitae.background_check = "Max retries reached"
            cvitae.background_date = datetime.utcnow()




# Cache to store tokens and expiration times
token_cache = {
    "access_token": None,
    "refresh_token": None,
    "expires_at": None
}

def get_token():
    """
    Retrieve a valid access token, refreshing it if necessary.
    """
    current_time = time.time()

    # If the access token is still valid, return it
    if token_cache["access_token"] and token_cache["expires_at"] > current_time:
        return token_cache["access_token"]

    # Retrieve credentials from environment variables
    username = os.getenv("SDUSERNAME")
    password = os.getenv("SDPASSWORD")
    basic_auth_token = os.getenv("SDBASIC_AUTH_TOKEN")

    print(username)
    print(password)
    print(basic_auth_token)

    if not username or not password or not basic_auth_token:
        raise HTTPException(status_code=500, detail="Authentication credentials are not properly configured.")

    # If no valid token, request a new one
    try:
        response = requests.post(
            "https://botai.smartdataautomation.com/api/o/token/",
            headers={
                "Authorization": f"Basic {basic_auth_token}"
            },
            data={
                "username": username,
                "password": password,
                "grant_type": "password"
            }
        )
        response.raise_for_status()
        token_data = response.json()

        # Update the token cache
        token_cache["access_token"] = token_data["access_token"]
        token_cache["refresh_token"] = token_data.get("refresh_token")
        token_cache["expires_at"] = current_time + token_data["expires_in"] - 10  # Buffer for safety

        return token_data["access_token"]

    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve token: {str(e)}")


def process_existing_vitae_records(
    cvitae_ids: List[int],
    offerId: int,
    skills_list: List[str],
    city_offer: str,
    age_offer: str,
    genre_offer: str,
    experience_offer: int,
    db: Session
):
    """
    Process existing CVitae records by their IDs and create/update associated VitaeOffer records.
    This does not delete CVitae records on failure.
    """
    try:
        # Fetch CVitae records
        cvitae_records = db.query(CVitae).filter(CVitae.Id.in_(cvitae_ids)).all()
        if not cvitae_records or len(cvitae_records) != len(cvitae_ids):
            raise HTTPException(status_code=404, detail="One or more CVitae records not found.")

        # Prepare cv_texts for GPT processing
        cv_texts = [cv.cvtext for cv in cvitae_records]

        response_json = parse_prompt(cv_texts, skills_list,
                                     city_offer, age_offer,
                                     genre_offer, experience_offer)
        if response_json is None:
            print("Error processing the OpenAI response")
            raise Exception("Error processing the OpenAI response")

        # Prepare the GPT prompt
        candidates = response_json.get("candidatos", [])

        # Process each candidate
        for idx, (cvitae, candidate_data) in enumerate(zip(cvitae_records,
                                                           candidates)):
            try:
                # Update/Create VitaeOffer record
                vitae_offer = db.query(VitaeOffer).filter(
                    VitaeOffer.cvitaeId == cvitae.Id,
                    VitaeOffer.offerId == offerId
                ).first()

                if vitae_offer:
                    # Update existing VitaeOffer
                    vitae_offer.ai_response = json.dumps(candidate_data)
                    vitae_offer.response_score = candidate_data.get("score", 0)
                    vitae_offer.status = "pending"
                else:
                    # Create new VitaeOffer
                    vitae_offer = VitaeOffer(
                        cvitaeId=cvitae.Id,
                        offerId=offerId,
                        status="pending",
                        ai_response=json.dumps(candidate_data),
                        response_score=candidate_data.get("score", 0),
                    )
                    db.add(vitae_offer)
            except Exception as e:
                print(traceback.format_exc())
                print(f"Error processing: {str(cvitae)}\nError: {str(e)}")

        db.commit()

    except Exception as e:
        # Rollback changes if something goes wrong
        print(traceback.format_exc())
        db.rollback()
        print(f"Error processing existing CVitae records: {str(e)}")
        raise HTTPException(status_code=500, detail="An error occurred while processing CVitae records.")

def extract_json(raw_response):
    # Try to extract JSON block inside ```json ... ```
    match = re.search(r"```json\s*(\{.*?\})\s*```", raw_response, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    else:
        # Try to parse entire response as JSON (if it's just JSON)
        try:
            return json.loads(raw_response)
        except json.JSONDecodeError:
            print("Failed to parse JSON.")
            return None