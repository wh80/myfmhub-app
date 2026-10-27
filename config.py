import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv()  

class Config:
    # required, with no fallback: a missing value stops the app starting (KeyError) rather than
    # running with a guessable key
    SECRET_KEY = os.environ['SECRET_KEY']
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')  # Changed this
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    OTP_PEPPER = os.environ['OTP_PEPPER']
    # set AUTH_COOKIE_SECURE=false only if serving over plain http somewhere other than localhost
    AUTH_COOKIE_SECURE = os.environ.get('AUTH_COOKIE_SECURE', 'true').lower() == 'true'
    RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
    EMAIL_FROM = os.environ.get('EMAIL_FROM', 'MyFmHub <notifications@app.myfmhub.com>')
    SUPPORT_EMAIL = os.environ.get('SUPPORT_EMAIL', 'support@myfmhub.com')
    # file storage (DigitalOcean Spaces). With no bucket set, files are saved under LOCAL_FILE_FOLDER
    # instead (dev and tests), like email being logged when there's no Resend key
    DO_SPACES_ENDPOINT = os.environ.get('DO_SPACES_ENDPOINT')
    DO_SPACES_KEY = os.environ.get('DO_SPACES_KEY')
    DO_SPACES_SECRET = os.environ.get('DO_SPACES_SECRET')
    DO_SPACES_BUCKET = os.environ.get('DO_SPACES_BUCKET')
    LOCAL_FILE_FOLDER = os.environ.get('LOCAL_FILE_FOLDER', os.path.join(basedir, 'instance', 'files'))
    # a backstop for every request body: file uploads are limited to 20 MB (services/files.py) and
    # imports to 5 MB, so this only stops anything much bigger being read at all
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024