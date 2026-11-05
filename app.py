"""
Application Root Alias
Allows running `python app.py` or WSGI servers pointing to app:app
"""

import os
import sys

from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Explicitly load .env file from project root
dotenv_path = os.path.join(ROOT_DIR, '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)

from backend.app import create_app

app = create_app()

if __name__ == '__main__':
    host = os.getenv('FLASK_HOST', '127.0.0.1')
    port = int(os.getenv('PORT', os.getenv('FLASK_PORT', 5000)))
    # DEBUG is strictly disabled by default
    debug = os.getenv('FLASK_DEBUG', os.getenv('DEBUG', '0')).strip().lower() in ('1', 'true', 'yes', 'on')
    use_reloader = os.getenv('FLASK_USE_RELOADER', '0') == '1'
    app.run(host=host, port=port, debug=debug, use_reloader=use_reloader)
