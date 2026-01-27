
import os
import random
import string
import boto3
import logging
from fastapi import HTTPException
from app.baseController import ControllerBase
from app.user.userDTO import UserInsert, UserSoftDelete, UserUpdateUser
from botocore.exceptions import BotoCoreError, NoCredentialsError

from models.models import Users

logger = logging.getLogger(__name__)

class ServiceUser(ControllerBase[Users, UserInsert, UserUpdateUser, UserSoftDelete]): 
    ...

userServices = ServiceUser(Users)

AWS_REGION = os.getenv("AWS_REGION", "us-east-2")

ses_client = boto3.client("ses", region_name=AWS_REGION)

CONFIGURATION_SET = os.getenv("SES_CONFIGURATION_SET", "")

ENABLE_EMAILS = os.getenv("ENABLE_EMAILS", "false").lower() == "true"

EMAIL_LOGO_URL = os.getenv("EMAIL_LOGO_URL", "https://deepvoicelabs.com/logo-deeptalent.png")

def generate_temp_password(length=10):
    """Generates a random alphanumeric password."""
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def send_email_with_temp_password(email: str, temp_password: str):
    """Sends an email using AWS SES with the temporary password."""
    
    # AWS SES Configuration
    SENDER = "contabilidad@coleoptera.co"  # Change to your verified SES sender email
    CHARSET = "UTF-8"
    SUBJECT = "Bienvenido a DeepTalent.Ai - Su acceso temporal"

    BODY_TEXT = f"""
    ¡Bienvenido a DeepTalent.Ai!
    
    Estimado/a,

    Nos complace darle la bienvenida a DeepTalent.Ai.
    Para comenzar, hemos generado una contraseña temporal para usted:
    
    Contraseña Temporal: {temp_password}
    
    Haga clic en el siguiente enlace para acceder a su cuenta y actualizar su contraseña:
    
    https://deepvoicelabs.com/login
    
    Atentamente,
    El equipo de DeepTalent.Ai
    """

    BODY_HTML = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Bienvenido a DeepTalent.Ai</title>
    </head>
    <body>
        <table width="100%" style="background-color: #ffffff; max-width: 600px; margin: auto;">
            <tr>
                <td style="text-align: center; padding: 20px;">
                    <img src="{EMAIL_LOGO_URL}" alt="DeepTalent.Ai Logo" style="max-width: 20%; height: auto;">
                </td>
            </tr>
            <tr>
                <td style="text-align: center; padding: 20px; background-color: #000000; color: #ffffff; font-size: 24px; font-weight: bold;">
                    ¡Bienvenido a DeepTalent.Ai!
                </td>
            </tr>
            <tr>
                <td style="padding: 20px; font-size: 16px; color: #333333;">
                    <p>Estimado/a,</p>
                    <p>Nos complace darle la bienvenida a DeepTalent.Ai.</p>
                    <p>Para comenzar, hemos generado una contraseña temporal para usted:</p>
                    <p style="text-align: center; font-weight: bold; font-size: 18px;">{temp_password}</p>
                    <p>Haga clic en el botón a continuación para acceder a su cuenta y actualizar su contraseña:</p>
                    <p style="text-align: center;">
                        <a href="https://deepvoicelabs.com/login" style="background-color: #000000; color: #ffffff; padding: 12px 24px; text-decoration: none; font-size: 16px; border-radius: 5px;">Actualizar Contraseña</a>
                    </p>
                </td>
            </tr>
            <tr>
                <td style="text-align: center; padding: 20px; background-color: #000000; color: #ffffff; font-size: 14px;">
                    &copy; 2025 DeepTalent.Ai. Todos los derechos reservados.
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

    try:
        email_args = {
            "Source": SENDER,
            "Destination": {"ToAddresses": [email]},
            "Message": {
                "Subject": {"Data": SUBJECT, "Charset": CHARSET},
                "Body": {
                    "Html": {"Data": BODY_HTML, "Charset": CHARSET},
                    "Text": {"Data": BODY_TEXT, "Charset": CHARSET},
                },
            },
        }

        if CONFIGURATION_SET:
            email_args["ConfigurationSetName"] = CONFIGURATION_SET

        if ENABLE_EMAILS:
            response = ses_client.send_email(**email_args)
            print(f"Email sent successfully to {email}. Message ID: {response['MessageId']}")

    except Exception as e:
        logger.error(f"Email failed: {e}")


def send_email_with_temp_resetpassword(email: str, temp_password: str):
    """Sends an email using AWS SES with the temporary password."""
    
    # AWS SES Configuration
    SENDER = "contabilidad@coleoptera.co"  # Change to your verified SES sender email
    CHARSET = "UTF-8"
    SUBJECT = "Bienvenido a DeepTalent.Ai - Su acceso temporal"

    BODY_TEXT = f"""
    ¡Bienvenido a DeepTalent.Ai!
    
    Estimado/a,

    Has solicitado reestablecer tu contraseña.

    Hemos generado una contraseña temporal para usted:
    
    Contraseña Temporal: {temp_password}
    
    Haga clic en el siguiente enlace para acceder a su cuenta y actualizar su contraseña:
    
    https://deepvoicelabs.com/login
    
    Atentamente,
    El equipo de DeepTalent.Ai
    """

    BODY_HTML = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Bienvenido a DeepTalent.Ai</title>
    </head>
    <body>
        <table width="100%" style="background-color: #ffffff; max-width: 600px; margin: auto;">
            <tr>
                <td style="text-align: center; padding: 20px;">
                    <img src="{EMAIL_LOGO_URL}" alt="DeepTalent.Ai Logo" style="max-width: 20%; height: auto;">
                </td>
            </tr>
            <tr>
                <td style="padding: 20px; font-size: 16px; color: #333333;">
                    <p>Estimado/a,</p>
                    <p>Has solicitado reestablecer tu contraseña.</p>
                    <p>Hemos generado una contraseña temporal para usted:</p>
                    <p style="text-align: center; font-weight: bold; font-size: 18px;">{temp_password}</p>
                    <p>Haga clic en el botón a continuación para acceder a su cuenta y actualizar su contraseña:</p>
                    <p style="text-align: center;">
                        <a href="https://deepvoicelabs.com/login" style="background-color: #000000; color: #ffffff; padding: 12px 24px; text-decoration: none; font-size: 16px; border-radius: 5px;">Actualizar Contraseña</a>
                    </p>
                </td>
            </tr>
            <tr>
                <td style="text-align: center; padding: 20px; background-color: #000000; color: #ffffff; font-size: 14px;">
                    &copy; 2025 DeepTalent.Ai. Todos los derechos reservados.
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

    try:
        email_args = {
            "Source": SENDER,
            "Destination": {"ToAddresses": [email]},
            "Message": {
                "Subject": {"Data": SUBJECT, "Charset": CHARSET},
                "Body": {
                    "Html": {"Data": BODY_HTML, "Charset": CHARSET},
                    "Text": {"Data": BODY_TEXT, "Charset": CHARSET},
                },
            },
        }

        if CONFIGURATION_SET:
            email_args["ConfigurationSetName"] = CONFIGURATION_SET

        if ENABLE_EMAILS:
            response = ses_client.send_email(**email_args)
            print(f"Email sent successfully to {email}. Message ID: {response['MessageId']}")

    except Exception as e:
        logger.error(f"Email failed: {e}")
