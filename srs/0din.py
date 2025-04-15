import os
import json
import logging
import secrets
import search
import indexer
import sqlite3
import settings
import datetime
from flask import Flask, render_template, redirect, request, jsonify, flash, send_file, abort, session, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from colorlog import ColoredFormatter
from dotenv import load_dotenv
from database import get_db_connection
from scheduler import start_scheduler, schedule_tasks
from threading import Thread


load_dotenv()

# Logging configuration
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

def setup_jinja_filters(app):
    @app.template_filter('date')
    def format_date(value):
        if not value:
            return ""
        try:
            if isinstance(value, str):
                dt = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
            else:
                dt = value
            return dt.strftime('%b %d, %Y')
        except:
            return value
    
    @app.template_filter('filesizeformat')
    def filesizeformat(bytes, precision=2):
        """Format the value like a 'human-readable' file size (i.e. 13 KB, 4.1 MB, 102 bytes, etc)."""
        if bytes is None or bytes == '':
            return '0 bytes'
        
        bytes = float(bytes)
        
        if bytes == 0:
            return '0 bytes'
        elif bytes == 1:
            return '1 byte'
        
        abbrevs = (
            (1<<50, 'PB'),
            (1<<40, 'TB'),
            (1<<30, 'GB'),
            (1<<20, 'MB'),
            (1<<10, 'KB'),
            (1, 'bytes')
        )
        
        for factor, suffix in abbrevs:
            if bytes >= factor:
                break
        
        if suffix == 'bytes':
            precision = 0
        
        return '%.*f %s' % (precision, bytes / factor, suffix)

# Add this to your existing Flask app initialization
def configure_app(app):
    setup_jinja_filters(app)
    
    # Add static folder configuration if needed
    app.static_folder = 'static'
    app.static_url_path = '/static'
    
    return app

app = Flask(__name__)
app = configure_app(app)
app.secret_key = secrets.token_hex(16)

def setup_admin_credentials(username, password):
    hashed_password = generate_password_hash(password)
    with open('credentials.json', 'w') as f:
        json.dump({'username': username, 'password': hashed_password}, f)

def load_credentials():
    if os.path.exists('credentials.json'):
        with open('credentials.json') as f:
            return json.load(f)
    return {'username': 'admin', 'password': generate_password_hash('admin')}

@app.before_request
def check_setup():
    ssl_enabled = os.getenv("ENABLE_SSL") == "true"
    https_redirect_enabled = os.getenv("ENABLE_HTTPS_REDIRECT") == "true"

    if ssl_enabled:
        if not request.is_secure:
            url = request.url.replace("http://", "https://", 1)
            return redirect(url, code=301)
        return

    if https_redirect_enabled:
        if not request.is_secure and request.headers.get('X-Forwarded-Proto', 'http') != 'https':
            url = request.url.replace("http://", "https://", 1)
            return redirect(url, code=301)

    if request.path.startswith('/static') or request.endpoint in ['setup', 'login']:
        return

    if not os.path.exists('credentials.json'):
        return redirect(url_for('setup'))

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if os.path.exists('credentials.json'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        password_confirmation = request.form['password_confirmation']

        if (password_confirmation != password):
            flash("Passwords do not match", 'error')
            return redirect(url_for('setup'))

        setup_admin_credentials(username, password)
        return redirect(url_for('login'))
    
    return render_template('setup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        credentials = load_credentials()
        if username == credentials['username'] and check_password_hash(credentials['password'], password):
            session['logged_in'] = True
            return redirect(url_for('admin'))
        else:
            return 'Invalid credentials', 401
    return render_template('login.html')

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    config = settings.return_all()

    if request.method == 'POST':
        for key in config:
            if key in request.form:
                try:
                    config[key] = json.loads(request.form[key])
                except ValueError:
                    config[key] = request.form[key]

    return render_template('admin.html', config=config)

@app.route('/shutdown', methods=['POST'])
def shutdown():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    func()
    return 'Server shutting down...'

@app.route('/restart', methods=['POST'])
def restart():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    func()
    os.execv(__file__, ['python'] + [__file__])
    return 'Server restarting...'

@app.route('/indexer', methods=['POST'])
def trigger_indexer():
    if not session.get('logged_in'):
        return "Unauthorized", 401

    conn = get_db_connection()
    path = request.json.get('path')
    if not path:
        return "Path is required", 400

    try:
        indexer.indexer(path, conn)
        return "Indexer run successfully", 200
    except Exception as e:
        return f"An error occurred: {str(e)}", 500
    finally:
        conn.close()

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/global_search', methods=['POST'])
def global_search_route():
    conn = get_db_connection()
    query = request.form.get('query')
    category = request.form.get('category')
    if category == 'all':
        category = None

    results = search.global_search(query, settings.get_setting("known_nodes"), settings.get_setting("NODE_ID"), conn, "name", category)
    conn.close()
    return render_template('results.html', query=query, category=category, results=results)

@app.route('/json/global_search', methods=['POST'])
def global_search_json():
    conn = get_db_connection()
    query = request.form.get('query')
    category = request.form.get('category')
    if category == 'all':
        category = None

    results = search.global_search(query, settings.get_setting("known_nodes"), settings.get_setting("NODE_ID"), conn, "name", category)
    conn.close()
    return jsonify(results)

@app.route('/localsearch', methods=['POST'])
def localsearch_endpoint():
    conn = get_db_connection()
    data = request.get_json()
    search_term = data.get('search_term')
    search_type = data.get('search_type', 'name')
    category = data.get('category')

    logger.debug(f"Received request for local search: search_term={search_term}, search_type={search_type}, category={category}")

    matches = search.local_search(search_term, settings.get_setting("NODE_ID"), conn, search_type, category)
    conn.close()
    return jsonify(matches), 200

@app.route('/md5_search/<md5_hash>')
def md5_search(md5_hash):
    try:
        conn = get_db_connection()
        results = search.global_search(md5_hash, settings.get_setting("known_nodes"), settings.get_setting("NODE_ID"), conn, "md5")
        return render_template('md5_results.html', md5_hash=md5_hash, results=results)
    except Exception as e:
        logger.error(f"Error during MD5 search: {e}")
        return "An error occurred during the search."
    finally:
        conn.close()

@app.route('/json/md5_search/<md5_hash>')
def md5_search_json(md5_hash):
    conn = get_db_connection()
    results = search.global_search(md5_hash, settings.get_setting("known_nodes"), settings.get_setting("NODE_ID"), conn, "md5")
    conn.close()
    return jsonify(results)

@app.route('/download/<md5_hash>')
def download_file(md5_hash):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT path FROM files WHERE md5_hash = ?;", (md5_hash,))
        result = cursor.fetchone()

        if result:
            path = result[0]
            cursor.execute("UPDATE files SET download_count = download_count + 1 WHERE md5_hash = ?;", (md5_hash,))
            conn.commit()
            return send_file(path)
        else:
            abort(404, description="File not found")
    finally:
        cursor.close()
        conn.close()

@app.route('/json/nodes')
def nodes():
    return jsonify(list(settings.get_setting("known_nodes")))

@app.route('/total_file_size', methods=['GET'])
def total_file_size():
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(file_size) FROM files;")
        result = cursor.fetchone()
        total_size = result[0] if result and result[0] is not None else 0
        return jsonify({'total_file_size': total_size})
    except sqlite3.Error as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/preview/<path:filename>', methods=['GET'])
def serve_preview(filename):
    try:
        shared_directory = settings.get_setting("DIRECTORY")
        hidden_directory = os.path.join(shared_directory, '.previews')
        preview_file_path = os.path.join(hidden_directory, f"{filename}")

        if not os.path.exists(preview_file_path):
            logger.warning(f"Preview file not found: {preview_file_path}")
            abort(404)

        return send_file(preview_file_path, mimetype='image/webp')
    except Exception as e:
        logger.error(f"Error serving preview for {filename}: {e}")
        abort(500)

@app.route('/announce', methods=['POST'])
def announce_endpoint():
    data = request.json
    node_id = data.get("node_id")
    response_url = data.get("response_url")
    received_known_nodes = data.get("known_nodes", [])
    logger.debug(f"Handling Announcement From {node_id}")
    known_nodes = settings.get_setting("known_nodes")
    known_nodes.add(node_id)
    known_nodes.update(received_known_nodes)
    settings.set_setting("known_nodes", known_nodes)
    return jsonify({"known_nodes": list(known_nodes)}), 200

@app.route('/heartbeat', methods=['GET'])
def heartbeat():
    return jsonify({"status": "alive", "message": "Heartbeat response from the node"}), 200

@app.route('/total_files', methods=['GET'])
def total_files():
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM files;")
        result = cursor.fetchone()
        total_files = result[0] if result and result[0] is not None else 0
        return jsonify({'total_files': total_files})
    except sqlite3.Error as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/total_files_all', methods=['GET'])
def total_files_all():
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM files;")
        result = cursor.fetchone()
        total_files = result[0] if result and result[0] is not None else 0
        known_nodes = settings.get_setting("known_nodes")
        for node in known_nodes:
            node_url = f"http://{node}:{os.getenv('NODE_PORT', 5000)}/total_files"
            response = requests.get(node_url)
            if response.status_code == 200:
                total_files += response.json()['total_files']
        return jsonify({'total_files': total_files})
    except sqlite3.Error as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

def run_background_tasks():
    start_scheduler()
    schedule_tasks()
    # You can also call indexing and peer discovery here if you want

def start_background_tasks():
    Thread(target=run_background_tasks, daemon=True).start()

if __name__ == '__main__':
    start_background_tasks()
    app.run(host='0.0.0.0', port=int(os.getenv("NODE_PORT", 5000)))

