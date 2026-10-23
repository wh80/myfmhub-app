import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv()  

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-this')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')  # Changed this
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    OTP_PEPPER = os.environ.get('OTP_PEPPER')
    # set AUTH_COOKIE_SECURE=false only if serving over plain http somewhere other than localhost
    AUTH_COOKIE_SECURE = os.environ.get('AUTH_COOKIE_SECURE', 'true').lower() == 'true'
    RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
    EMAIL_FROM = os.environ.get('EMAIL_FROM', 'MyFmHub <notifications@app.myfmhub.com>')
    SUPPORT_EMAIL = os.environ.get('SUPPORT_EMAIL', 'support@myfmhub.com')