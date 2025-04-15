import os
import sqlite3
import logging
from colorlog import ColoredFormatter

# Logging setup
log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
formatter = ColoredFormatter(
    "%(asctime)s - %(name)s - %(log_color)s%(levelname)s%(reset)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    log_colors={
        'DEBUG': 'cyan',
        'INFO': 'green',
        'WARNING': 'yellow',
        'ERROR': 'red',
        'CRITICAL': 'bold_red',
    }
)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
logger.addHandler(console_handler)

# Path to SQLite database file
DB_PATH = os.getenv('SQLITE_DB_PATH', 'database.db')

def get_db_connection():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        logger.info("SQLite database connection established.")
        return conn
    except sqlite3.Error as e:
        logger.error(f"SQLite connection failed: {e}")
        raise

def put_connection(conn):
    conn.close()
