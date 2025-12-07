import os
import sqlite3
import json
import subprocess
import threading
import time
import zipfile
import shutil
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, send_file, abort, redirect, url_for
from werkzeug.utils import secure_filename
import uuid
import urllib.request

# Telegram Bot Imports
try:
    import telebot
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    print("Warning: pyTelegramBotAPI not installed. Telegram features disabled.")

app = Flask(__name__)
app.secret_key = 'tiktok_tracker_secret_key_2024'

# Configuration
DATABASE_PATH = 'data/trackui.db'
DOWNLOADS_PATH = 'data/downloads'
AVATARS_PATH = 'data/avatars'
MAX_RETRIES = 3
RETRY_DELAY = 5

# Global Bot Instance
bot = None
bot_thread = None

# Rate limiting bypass configuration
RATELIMIT_BYPASS = True
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0'
]
REQUEST_DELAY = 2  # seconds between requests
TIMEOUT_THRESHOLD = 90  # seconds
DOWNLOAD_TIMEOUT = 600  # seconds per-user download maximum (failsafe)

# Global variables for tracking
download_progress = {}
sync_status = {'running': False, 'last_sync': None, 'current_user': None, 'timeout_users': [], 'current_timeout': False}
sync_logs = []

# This queue powers the Download Manager UI. We'll also push long-running non-download tasks (like Sync All) here.
global_download_queue = []  # List of all downloads/tasks with their status
active_downloads = {}  # Currently active downloads/tasks keyed by 'username' label

# Track running download processes and controls (pause/resume)
download_processes = {}   # username -> subprocess.Popen
_download_controls = {}   # username -> {'pause': bool}

# Special label for representing the Sync All task in the Download Manager
SYNC_QUEUE_USERNAME = 'Sync All'

# Scheduler
scheduler_started = False
scheduler_logs = []  # Store scheduler activity logs


timeout_count = 0
user_agent_index = 0

def init_database():
    """Initialize the SQLite database with required tables."""
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    print(f"Initializing database at: {os.path.abspath(DATABASE_PATH)}")
    
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # Check if platform column exists, migrate if needed
    try:
        cursor.execute("SELECT platform FROM users LIMIT 1")
    except sqlite3.OperationalError:
        # Platform column doesn't exist, need to migrate
        print("Migrating users table to add platform support...")
        
        # Backup existing data
        cursor.execute("SELECT * FROM users")
        existing_users = cursor.fetchall()
        
        # Drop and recreate users table with platform
        cursor.execute("DROP TABLE IF EXISTS users_old")
        cursor.execute("ALTER TABLE users RENAME TO users_old")
        
    # Users table with platform support
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'tiktok',
            display_name TEXT,
            profile_picture TEXT,
            follower_count INTEGER DEFAULT 0,
            following_count INTEGER DEFAULT 0,
            video_count INTEGER DEFAULT 0,
            is_tracking BOOLEAN DEFAULT 1,
            last_sync TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            download_count INTEGER DEFAULT 0,
            last_download TIMESTAMP,
            UNIQUE(username, platform)
        )
    ''')
    
    # Migrate existing data if we have backup
    try:
        cursor.execute("SELECT COUNT(*) FROM users_old")
        old_count = cursor.fetchone()[0]
        if old_count > 0:
            print(f"Migrating {old_count} existing users to TikTok platform...")
            cursor.execute('''
                INSERT INTO users (username, platform, display_name, profile_picture, 
                                 follower_count, following_count, video_count, is_tracking,
                                 last_sync, created_at, download_count, last_download)
                SELECT username, 'tiktok', display_name, profile_picture,
                       follower_count, following_count, video_count, is_tracking,
                       last_sync, created_at, download_count, last_download
                FROM users_old
            ''')
        cursor.execute("DROP TABLE users_old")
    except sqlite3.OperationalError:
        # No old table, fresh install
        pass
    
    # Tags table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            color TEXT DEFAULT '#007bff',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Drop and recreate user_tags table to fix foreign key references
    try:
        cursor.execute('DROP TABLE IF EXISTS user_tags')
    except Exception:
        pass
    
    # User tags junction table with correct foreign key references
    cursor.execute('''
        CREATE TABLE user_tags (
            user_id INTEGER,
            tag_id INTEGER,
            PRIMARY KEY (user_id, tag_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        )
    ''')

    # App settings table for persistent configuration
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    # Likes table for feed feature
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_path TEXT NOT NULL UNIQUE,
            liked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Insert defaults if not present
    def ensure_setting(k, v):
        cur = conn.execute('SELECT value FROM settings WHERE key = ?', (k,)).fetchone()
        if not cur:
            conn.execute('INSERT INTO settings (key, value) VALUES (?, ?)', (k, v))

    ensure_setting('skip_existing', 'true')
    ensure_setting('schedule_enabled', 'false')
    ensure_setting('schedule_frequency', 'daily')  # 'daily' or 'weekly'
    ensure_setting('schedule_time', '03:00')       # HH:MM 24h
    ensure_setting('schedule_day', '0')            # 0=Monday .. 6=Sunday (for weekly)
    ensure_setting('schedule_last_run', '')        # ISO timestamp of last run
    ensure_setting('download_timeout', '600')       # per-user download timeout seconds
    ensure_setting('instagram_active_cookies', '')  # filename of active IG cookies file
    ensure_setting('profile_feed_videos_only', 'false')  # Hide images in per-profile feeds
    ensure_setting('instagram_following_cookies', '')  # filename of cookies for following feature
    
    conn.commit()
    conn.close()
    print("Database initialized successfully with all tables created.")
    
def verify_database():
    """Verify that database tables exist and are accessible."""
    try:
        conn = get_db_connection()
        
        # Check if tables exist
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_names = [table['name'] for table in tables]
        print(f"Database tables found: {table_names}")
        
        # Test basic queries
        user_count = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        tag_count = conn.execute('SELECT COUNT(*) FROM tags').fetchone()[0]
        print(f"Users: {user_count}, Tags: {tag_count}")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"Database verification failed: {e}")
        return False

def get_db_connection():
    """Get database connection with row factory for dict-like access."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# Settings helpers

def get_setting(key, default=None):
    try:
        conn = get_db_connection()
        row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        conn.close()
        if row and row['value'] is not None:
            return row['value']
    except Exception:
        pass
    return default

def set_setting(key, value):
    conn = get_db_connection()
    conn.execute('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))
    conn.commit()
    conn.close()


def get_bool_setting(key, default=False):
    val = str(get_setting(key, 'true' if default else 'false')).strip().lower()
    return val in ('1', 'true', 'yes', 'on')

def run_gallery_dl_json(username, platform='tiktok', retry_count=0):
    """Extract metadata from TikTok profile using gallery-dl with rate limiting bypass."""
    global user_agent_index, timeout_count
    
    try:
        url = f"https://www.tiktok.com/@{username}" if platform=='tiktok' else f"https://www.instagram.com/{username}/"
        cmd = ['gallery-dl', '--dump-json', '--no-download']
        
        # Add rate limiting bypass options if enabled
        if RATELIMIT_BYPASS:
            # Rotate user agents
            user_agent = USER_AGENTS[user_agent_index % len(USER_AGENTS)]
            cmd.extend(['--option', f'extractor.user-agent={user_agent}'])
            
            # Add random delays
            import random
            delay = random.uniform(REQUEST_DELAY, REQUEST_DELAY + 2)
            if retry_count > 0:
                delay *= (retry_count + 1)  # Increase delay on retries
            cmd.extend(['--option', 'extractor.sleep-request=3-7'])  # Random sleep between 3 and 7 seconds
            
            # Add headers to mimic browser behavior
            cmd.extend([
                '--option', 'extractor.headers.Accept=text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                '--option', 'extractor.headers.Accept-Language=en-US,en;q=0.5',
                '--option', 'extractor.headers.Accept-Encoding=gzip, deflate',
                '--option', 'extractor.headers.DNT=1',
                '--option', 'extractor.headers.Connection=keep-alive',
                '--option', 'extractor.headers.Upgrade-Insecure-Requests=1'
            ])
            
            user_agent_index += 1
            
            # Add delay before request
            if delay > 0:
                time.sleep(delay)
        
        cmd.append(url)
        
        # Use longer timeout and track timing
        start_time = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_THRESHOLD)
        end_time = time.time()
        duration = end_time - start_time
        
        # Check if request was slow (potential rate limiting)
        if duration > TIMEOUT_THRESHOLD * 0.8:  # 80% of timeout threshold
            print(f"Slow request detected for {username}: {duration:.2f}s")
        
        if result.returncode != 0:
            error_msg = result.stderr.lower()
            # Check for rate limiting indicators
            if any(indicator in error_msg for indicator in ['rate limit', '429', 'too many requests', 'blocked']):
                if retry_count < MAX_RETRIES and RATELIMIT_BYPASS:
                    print(f"Rate limit detected for {username}, retrying with different settings...")
                    time.sleep(RETRY_DELAY * (retry_count + 1))  # Exponential backoff
                    return run_gallery_dl_json(username, retry_count + 1)
            return None, f"gallery-dl error: {result.stderr}"
            
        # Parse JSON output - handle both line-by-line and array formats
        metadata = []
        out = result.stdout.strip()
        if not out:
            return [], None
        
        # Try parsing as a single JSON value (array or object)
        try:
            data = json.loads(out)
            if isinstance(data, list):
                metadata.extend(data)
            else:
                metadata.append(data)
        except json.JSONDecodeError:
            # Fallback: parse line-by-line JSONL
            for line in out.split('\n'):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    metadata.append(data)
                except json.JSONDecodeError:
                    continue
        
        # Reset timeout count on success
        timeout_count = 0
        return metadata, None
        
    except subprocess.TimeoutExpired:
        timeout_count += 1
        error_msg = f"Request timed out after {TIMEOUT_THRESHOLD}s"
        
        # Try retry with different settings if rate limiting bypass is enabled
        if retry_count < MAX_RETRIES and RATELIMIT_BYPASS:
            print(f"Timeout for {username}, attempt {retry_count + 1}/{MAX_RETRIES}, retrying...")
            time.sleep(RETRY_DELAY * (retry_count + 1))
            return run_gallery_dl_json(username, retry_count + 1)
        
        return None, error_msg
    except Exception as e:
        return None, f"Error: {str(e)}"


def run_gallery_dl_json_instagram(username, retry_count=0):
    """Extract metadata for Instagram profile using gallery-dl with optional cookies."""
    try:
        url = f"https://www.instagram.com/{username}/"
        cmd = ['gallery-dl', '--dump-json', '--no-download']
        # Use active cookies if set
        active = get_setting('instagram_active_cookies','') or ''
        cookie_path = os.path.join('data','cookies','instagram', active) if active else ''
        if active and os.path.exists(cookie_path):
            cmd.extend(['--cookies', cookie_path])
        result = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=TIMEOUT_THRESHOLD)
        if result.returncode != 0:
            return None, f"gallery-dl error: {result.stderr}"
        out = result.stdout.strip()
        if not out:
            return [], None
        metadata = []
        try:
            data = json.loads(out)
            if isinstance(data, list):
                metadata.extend(data)
            else:
                metadata.append(data)
        except json.JSONDecodeError:
            for line in out.split('\n'):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    metadata.append(data)
                except json.JSONDecodeError:
                    continue
        return metadata, None
    except Exception as e:
        return None, f"Error: {str(e)}"

def run_gallery_dl_download(username, progress_callback=None, platform='tiktok'):
    """Download content from TikTok profile using gallery-dl.
    Uses a per-user download archive to avoid re-downloading existing media (configurable).
    Includes a failsafe timeout to avoid getting stuck.

    Returns: (success: bool, output: str, file_count: int, paused: bool)
    """
    try:
        if platform == 'tiktok':
            url = f"https://www.tiktok.com/@{username}"
        elif platform == 'instagram':
            url = f"https://www.instagram.com/{username}/"
        elif platform == 'coomer':
             url = f"https://coomer.su/onlyfans/user/{username}"
        else:
             url = f"https://www.tiktok.com/@{username}"
             
        output_dir = os.path.join(DOWNLOADS_PATH, platform, username)
        os.makedirs(output_dir, exist_ok=True)
        
        # Per-user archive file to skip items already downloaded in previous runs
        archive_path = os.path.join(output_dir, 'download-archive.txt')
        
        cmd = [
            'gallery-dl',
            '--dest', output_dir,
            '--write-metadata',
            '--write-info-json',
        ]
        
        # Cookies for Instagram if active is set
        if platform == 'instagram':
            active_cookies = get_setting('instagram_active_cookies', '') or ''
            cookie_path = os.path.join('data', 'cookies', 'instagram', active_cookies) if active_cookies else ''
            if active_cookies and os.path.exists(cookie_path):
                cmd.extend(['--cookies', cookie_path])
        
        if get_bool_setting('skip_existing', True):
            cmd.extend(['--download-archive', archive_path])
        
        cmd.append(url)
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        download_processes[username] = process
        _download_controls.setdefault(username, {'pause': False})
        
        output_lines = []
        file_count = 0
        paused_flag = False
        
        # Reader thread to avoid blocking readline indefinitely
        # Reader thread to avoid blocking readline indefinitely
        def _reader():
            nonlocal file_count, paused_flag
            try:
                for line in iter(process.stdout.readline, ''):
                    if not line:
                        break
                    # Check for pause request
                    if _download_controls.get(username, {}).get('pause'):
                        paused_flag = True
                        try:
                            process.terminate()
                        except Exception:
                            pass
                        break
                    s = line.strip()
                    output_lines.append(s)
                    low = s.lower()
                    
                    # Filter for cleaner UI feedback
                    # Only show actual file downloads or specific status updates
                    is_download_msg = 'download' in low and any(ext in low for ext in ['.mp4', '.jpg', '.jpeg', '.png', '.gif'])
                    is_already = 'already downloaded' in low
                    
                    if is_download_msg:
                        file_count += 1
                        if progress_callback:
                            # Clean up the message for UI
                            clean_msg = s
                            if '[downloader.http]' in s:
                                clean_msg = s.split(']', 1)[-1].strip()
                            if '[gallery-dl]' in s:
                                clean_msg = s.split(']', 1)[-1].strip()
                            progress_callback(file_count, clean_msg)
                    elif progress_callback and not any(x in low for x in ['[warning]', '[error]', 'connection', 'redirect']):
                        # Show other non-warning messages occasionally
                        pass
            except Exception:
                pass
        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        
        # Resolve timeout from settings (fallback to default)
        try:
            timeout_secs = int(get_setting('download_timeout', str(DOWNLOAD_TIMEOUT)) or DOWNLOAD_TIMEOUT)
        except Exception:
            timeout_secs = DOWNLOAD_TIMEOUT
        
        try:
            process.wait(timeout=timeout_secs)
        except subprocess.TimeoutExpired:
            try:
                process.terminate()
            except Exception:
                pass
            try:
                # Give it a moment to terminate, then kill if still alive
                time.sleep(2)
                if process.poll() is None:
                    process.kill()
            except Exception:
                pass
            output_lines.append(f"Download for @{username} timed out after {timeout_secs}s and was terminated.")
            del download_processes[username]
            return False, '\n'.join(output_lines), file_count, False
        
        # Clear from registry
        download_processes.pop(username, None)
        
        if paused_flag:
            output_lines.append("Download paused by user")
            return False, '\n'.join(output_lines), file_count, True
        
        return process.returncode == 0, '\n'.join(output_lines), file_count, False
    except Exception as e:
        return False, f"Error: {str(e)}", 0


def perform_download_instagram_aux(username, kind='stories'):
    """Download Instagram stories or highlights using gallery-dl with cookies if available.
    Returns (success: bool, output: str, file_count: int)
    """
    assert kind in ('stories','highlights')
    output_dir = os.path.join(DOWNLOADS_PATH, 'instagram', username, kind)
    os.makedirs(output_dir, exist_ok=True)
    archive_path = os.path.join(output_dir, 'download-archive.txt')
    
    # Use correct URL formats for Instagram
    if kind == 'stories':
        target = f"https://www.instagram.com/stories/{username}/"
    elif kind == 'highlights':
        target = f"https://www.instagram.com/{username}/highlights/"
    else:
        raise ValueError(f"Unsupported kind: {kind}")
    
    cmd = [
        'gallery-dl',
        '--config', 'gallery-dl.conf',
        '--dest', output_dir,
        '--write-metadata',
        '--write-info-json',
        '--verbose'  # Add verbose output for better debugging
    ]
    
    # Add cookies if available
    active = get_setting('instagram_active_cookies','') or ''
    cookie_path = os.path.join('data','cookies','instagram', active) if active else ''
    if active and os.path.exists(cookie_path):
        cmd.extend(['--cookies', cookie_path])
        print(f"Using cookies for {kind}: {active}")
    else:
        print(f"No cookies available for {kind} download of {username}")
    
    # Add archive for skip existing
    if get_bool_setting('skip_existing', True):
        cmd.extend(['--download-archive', archive_path])
    
    cmd.append(target)
    
    print(f"Running command: {' '.join(cmd)}")
    
    try:
        # Get timeout from settings
        timeout_secs = int(get_setting('download_timeout', str(DOWNLOAD_TIMEOUT)) or DOWNLOAD_TIMEOUT)
        
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            timeout=timeout_secs
        )
        
        output = result.stdout + result.stderr
        print(f"Instagram {kind} download output for {username}:")
        print(output)
        
        # Count downloaded files
        file_count = 0
        for line in output.split('\n'):
            line_lower = line.lower()
            if 'download' in line_lower and any(ext in line_lower for ext in ['.mp4', '.jpg', '.jpeg', '.png', '.gif']):
                file_count += 1
        
        success = result.returncode == 0
        if not success:
            print(f"Instagram {kind} download failed for {username}: return code {result.returncode}")
        else:
            print(f"Instagram {kind} download completed for {username}: {file_count} files")
            
        return success, output, file_count
        
    except subprocess.TimeoutExpired:
        error_msg = f"Instagram {kind} download for {username} timed out after {timeout_secs}s"
        print(error_msg)
        return False, error_msg, 0
    except Exception as e:
        error_msg = f"Error downloading Instagram {kind} for {username}: {str(e)}"
        print(error_msg)
        return False, error_msg, 0

def list_user_status(platform_filter=None):
    """Get status of all tracked users with download counts."""
    conn = get_db_connection()
    if platform_filter in ('tiktok','instagram'):
        users = conn.execute('''
            SELECT u.*, COUNT(DISTINCT ut.tag_id) as tag_count
            FROM users u
            LEFT JOIN user_tags ut ON u.id = ut.user_id
            WHERE u.platform = ?
            GROUP BY u.id
            ORDER BY u.created_at DESC
        ''', (platform_filter,)).fetchall()
    else:
        users = conn.execute('''
            SELECT u.*, COUNT(DISTINCT ut.tag_id) as tag_count
            FROM users u
            LEFT JOIN user_tags ut ON u.id = ut.user_id
            GROUP BY u.id
            ORDER BY u.created_at DESC
        ''').fetchall()
    conn.close()
    
    user_list = []
    for user in users:
        user_dict = dict(user)
        
        # Count downloaded files
        user_dir = os.path.join(DOWNLOADS_PATH, user['username'])
        file_count = 0
        if os.path.exists(user_dir):
            for root, dirs, files in os.walk(user_dir):
                file_count += len([f for f in files if f.lower().endswith(('.mp4', '.jpg', '.jpeg', '.png', '.gif'))])
        
        user_dict['downloaded_files'] = file_count
        user_list.append(user_dict)
    
    return user_list

def create_user_zip(username, platform='tiktok'):
    """Create a ZIP file of all downloaded content for a user."""
    user_dir = os.path.join(DOWNLOADS_PATH, platform, username)
    if not os.path.exists(user_dir):
        return None
    
    zip_path = os.path.join(DOWNLOADS_PATH, f"{username}_content.zip")
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(user_dir):
            for file in files:
                file_path = os.path.join(root, file)
                arc_path = os.path.relpath(file_path, user_dir)
                zipf.write(file_path, arc_path)
    
    return zip_path

def test_tiktok_access():
    """Test connectivity to TikTok and gallery-dl availability."""
    try:
        # Test gallery-dl availability
        result = subprocess.run(['gallery-dl', '--version'], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return False, "gallery-dl not found or not working"
        
        # Test basic TikTok access
        test_cmd = ['gallery-dl', '--dump-json', '--no-download', 'https://www.tiktok.com/']
        result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=30)
        
        return True, "TikTok access working"
    except Exception as e:
        return False, f"Error testing access: {str(e)}"

def test_instagram_highlights_access(username):
    """Test Instagram highlights access for debugging."""
    try:
        # Test highlights access
        test_cmd = [
            'gallery-dl', 
            '--dump-json', 
            '--no-download',
            f'https://www.instagram.com/{username}/highlights/'
        ]
        
        active = get_setting('instagram_active_cookies','') or ''
        cookie_path = os.path.join('data','cookies','instagram', active) if active else ''
        if active and os.path.exists(cookie_path):
            test_cmd.extend(['--cookies', cookie_path])
        
        result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=30)
        
        output = result.stdout + result.stderr
        success = result.returncode == 0
        
        return success, output
    except Exception as e:
        return False, f"Error testing Instagram highlights access: {str(e)}"

def add_to_global_queue(username, download_id=None):
    """Add a download to the global queue."""
    if download_id is None:
        download_id = f"{username}_{int(time.time())}"
    
    download_entry = {
        'id': download_id,
        'username': username,
        'status': 'queued',
        'start_time': time.time(),
        'end_time': None,
        'files_downloaded': 0,
        'total_files': 0,
        'current_file': '',
        'progress': 0,
        'logs': []
    }
    
    global_download_queue.append(download_entry)
    active_downloads[username] = download_id
    return download_id

def update_global_queue(username, **kwargs):
    """Update a download in the global queue."""
    if username not in active_downloads:
        return
    
    download_id = active_downloads[username]
    for entry in global_download_queue:
        if entry['id'] == download_id:
            entry.update(kwargs)
            
            # Calculate progress percentage
            if entry.get('total_files', 0) > 0:
                entry['progress'] = int((entry.get('files_downloaded', 0) / entry['total_files']) * 100)
            elif entry.get('files_downloaded', 0) > 0:
                entry['progress'] = min(entry['files_downloaded'] * 5, 95)  # Estimate
            
            # Mark as complete if status changed
            if kwargs.get('status') in ['completed', 'failed']:
                entry['end_time'] = time.time()
                if username in active_downloads:
                    del active_downloads[username]
            break

def get_global_download_status():
    """Get status of all downloads (excluding sync operations)."""
    # Clean up old completed downloads (keep last 20)
    if len(global_download_queue) > 20:
        # Remove old completed downloads
        completed = [d for d in global_download_queue if d['status'] in ['completed', 'failed']]
        if len(completed) > 10:
            # Keep only the 10 most recent completed downloads
            completed.sort(key=lambda x: x.get('end_time', 0), reverse=True)
            to_remove = completed[10:]
            for item in to_remove:
                global_download_queue.remove(item)
    
    # Filter out sync operations from the display
    user_downloads = [d for d in global_download_queue if d['username'] != SYNC_QUEUE_USERNAME]
    
    return {
        'total_downloads': len(user_downloads),
        'active_downloads': len([d for d in user_downloads if d['status'] in ['downloading', 'running']]),
        'completed_downloads': len([d for d in user_downloads if d['status'] == 'completed']),
        'failed_downloads': len([d for d in user_downloads if d['status'] == 'failed']),
        'downloads': sorted(user_downloads, key=lambda x: x['start_time'], reverse=True)
    }


def download_avatar_with_gallery_dl(username, platform='tiktok'):
    """Download user's avatar using gallery-dl directly or direct URL for Coomer."""
    try:
        os.makedirs(AVATARS_PATH, exist_ok=True)
        
        # SPECIAL HANDLING FOR COOMER
        if platform == 'coomer':
            try:
                import urllib.request as ul_req
                # Direct download from Coomer image server
                url = f"https://img.coomer.st/icons/onlyfans/{username}"
                filename = os.path.join(AVATARS_PATH, f"coomer_{username}.jpg")
                
                print(f"Downloading Coomer avatar from: {url}")
                
                # Use a proper user agent to avoid 403s
                req = ul_req.Request(
                    url, 
                    data=None, 
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                    }
                )
                
                with ul_req.urlopen(req, timeout=10) as response, open(filename, 'wb') as out_file:
                    shutil.copyfileobj(response, out_file)
                
                if os.path.exists(filename) and os.path.getsize(filename) > 0:
                     return filename
            except Exception as e:
                 print(f"Direct Coomer avatar download failed: {e}")
                 # Fallback to gallery-dl if needed, but usually Coomer requires this direct link
                 pass

        # Set correct URL based on platform - use dedicated extractors for Instagram
        if platform == 'instagram':
            url = f"https://www.instagram.com/{username}/avatar/"
        elif platform == 'coomer':
             # Fallback URL for gallery-dl
            url = f"https://coomer.su/onlyfans/user/{username}"
        else:
            url = f"https://www.tiktok.com/@{username}"
        
        # Use gallery-dl to get avatar information
        cmd = ['gallery-dl', '--dump-json', '--no-download']
        
        # Add Instagram cookies if available
        if platform == 'instagram':
            active_cookies = get_setting('instagram_active_cookies', '') or ''
            cookie_path = os.path.join('data', 'cookies', 'instagram', active_cookies) if active_cookies else ''
            if active_cookies and os.path.exists(cookie_path):
                cmd.extend(['--cookies', cookie_path])
        
        # Add rate limiting bypass if enabled
        if RATELIMIT_BYPASS:
            global user_agent_index
            user_agent = USER_AGENTS[user_agent_index % len(USER_AGENTS)]
            cmd.extend(['--option', f'extractor.user-agent={user_agent}'])
            user_agent_index += 1
        
        cmd.append(url)
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_THRESHOLD)
        
        if result.returncode != 0:
            print(f"Failed to get avatar info for {username} ({platform}): {result.stderr}")
            if result.stderr:
                print(f"Gallery-dl stderr: {result.stderr[:500]}")
            return None
        
        if not result.stdout.strip():
            print(f"No output from gallery-dl for {username}")
            return None
        
        # Parse JSON to find avatar URL
        avatar_url = None
        out = result.stdout.strip()
        if out:
            try:
                # Try parsing as single JSON first
                try:
                    data = json.loads(out)
                    if isinstance(data, list):
                        metadata_list = data
                    else:
                        metadata_list = [data]
                except json.JSONDecodeError:
                    # Parse line by line
                    metadata_list = []
                    for line in out.split('\n'):
                        line = line.strip()
                        if line:
                            try:
                                metadata_list.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
                
                # Find avatar URL in metadata
                for item in metadata_list:
                    avatar_url = None
                    metadata_dict = None
                    
                    if isinstance(item, list) and len(item) >= 2:
                        # Handle gallery-dl's array format [type, data, metadata]
                        if platform == 'instagram' and len(item) >= 3:
                            # For Instagram avatar extractor, the URL is directly in item[1]
                            # and metadata is in item[2]
                            if isinstance(item[1], str) and item[1].startswith('http'):
                                avatar_url = item[1]  # Direct URL from Instagram avatar extractor
                                print(f"Found direct avatar URL for {username} ({platform}): {avatar_url[:100]}...")
                                break
                            # Also check metadata in item[2]
                            if isinstance(item[2], dict):
                                metadata_dict = item[2]
                        elif len(item) >= 3 and isinstance(item[2], dict):
                            metadata_dict = item[2]
                        elif isinstance(item[1], dict):
                            metadata_dict = item[1]
                        else:
                            continue
                    elif isinstance(item, dict):
                        metadata_dict = item
                    else:
                        continue
                    
                    # If we got direct URL, skip metadata parsing
                    if avatar_url:
                        break
                    
                    # Look for avatar URLs in metadata fields based on platform
                    if metadata_dict:
                        if platform == 'instagram':
                            # Instagram-specific avatar field names including dedicated extractor fields
                            avatar_url = (metadata_dict.get('display_url') or  # From Instagram avatar extractor
                                        metadata_dict.get('uploader_profile_image') or 
                                        metadata_dict.get('uploader_avatar') or
                                        metadata_dict.get('avatar_url') or 
                                        metadata_dict.get('profile_pic_url') or
                                        metadata_dict.get('profile_pic_url_hd') or
                                        metadata_dict.get('avatar'))
                            
                            # Try nested owner/user fields for Instagram
                            if not avatar_url:
                                for nested_key in ['user', 'owner', 'uploader_info']:
                                    if nested_key in metadata_dict and isinstance(metadata_dict[nested_key], dict):
                                        nested_data = metadata_dict[nested_key]
                                        avatar_url = (nested_data.get('profile_pic_url_hd') or 
                                                    nested_data.get('profile_pic_url') or
                                                    nested_data.get('avatar') or
                                                    nested_data.get('profile_picture'))
                                        if avatar_url:
                                            break
                        else:
                            # TikTok-specific avatar field names
                            avatar_url = (metadata_dict.get('avatarLarger') or 
                                        metadata_dict.get('avatarMedium') or 
                                        metadata_dict.get('avatarThumb') or
                                        metadata_dict.get('uploader_avatar') or 
                                        metadata_dict.get('avatar_url') or 
                                        metadata_dict.get('avatar') or
                                        metadata_dict.get('uploader_profile_image'))
                            
                            # Try nested author fields for TikTok
                            if not avatar_url and 'author' in metadata_dict:
                                author = metadata_dict['author']
                                if isinstance(author, dict):
                                    avatar_url = (author.get('avatarLarger') or 
                                                 author.get('avatarMedium') or 
                                                 author.get('avatarThumb') or
                                                 author.get('avatar'))
                        
                        if avatar_url:
                            print(f"Found avatar URL for {username} ({platform}): {avatar_url[:100]}...")
                            break
                
                if not avatar_url:
                    print(f"No avatar URL found for {username}")
                    return None
                
                # Download the avatar
                import urllib.request
                import urllib.parse
                
                # Determine file extension from URL or default to jpg
                parsed_url = urllib.parse.urlparse(avatar_url)
                ext = '.jpg'
                if parsed_url.path:
                    path_ext = os.path.splitext(parsed_url.path)[1].lower()
                    if path_ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']:
                        ext = path_ext
                
                local_path = os.path.join(AVATARS_PATH, f"{platform}_{username}{ext}")
                
                # Add user agent header to avoid blocks
                if RATELIMIT_BYPASS:
                    user_agent = USER_AGENTS[user_agent_index % len(USER_AGENTS)]
                    req = urllib.request.Request(avatar_url, headers={'User-Agent': user_agent})
                    with urllib.request.urlopen(req, timeout=60) as response:
                        with open(local_path, 'wb') as f:
                            f.write(response.read())
                else:
                    urllib.request.urlretrieve(avatar_url, local_path)
                
                print(f"Avatar cached for {username}: {local_path}")
                return local_path
                
            except Exception as e:
                print(f"Error parsing avatar data for {username}: {e}")
                return None
        
        return None
        
    except subprocess.TimeoutExpired:
        print(f"Avatar download timeout for {username}")
        return None
    except Exception as e:
        print(f"Error downloading avatar for {username}: {e}")
        return None

def try_get(d, keys, default=None):
    for k in keys:
        if isinstance(k, (list, tuple)):
            # Nested path
            cur = d
            ok = True
            for kk in k:
                if isinstance(cur, dict) and kk in cur:
                    cur = cur[kk]
                else:
                    ok = False
                    break
            if ok:
                return cur
        else:
            if k in d:
                return d[k]
    return default

def update_user_stats(username, platform='tiktok'):
    """Update user statistics from a profile for specified platform."""
    if platform == 'instagram':
        metadata, error = run_gallery_dl_json_instagram(username)
    elif platform == 'coomer':
        metadata, error = run_gallery_dl_json_coomer(username)
    else:
        metadata, error = run_gallery_dl_json(username, platform)
    
    # Handle timeout specifically
    if error and "timed out" in error.lower():
        # Add to timeout users list for status tracking
        if username not in sync_status.get('timeout_users', []):
            sync_status['timeout_users'].append(username)
        sync_status['current_timeout'] = True
        return False, f"⏱️ {error} (retried {MAX_RETRIES} times)"
    
    if error or not metadata:
        return False, error or "No metadata retrieved"
    
    # Extract profile information from metadata
    profile_data = {}
    avatar_url = ''
    # Flatten any nested lists in metadata
    flat_meta = []
    for it in metadata:
        if isinstance(it, list):
            flat_meta.extend(it)
        else:
            flat_meta.append(it)
    for item in flat_meta:
        if isinstance(item, dict) and ('uploader' in item or 'author' in item or item.get('extractor') == 'tiktok'):
            display_name = item.get('uploader') or try_get(item, [('author','nickname'), 'author', 'creator'], username)
            # Try multiple keys for avatar URL, nested too
            avatar_url = (try_get(item, ['uploader_avatar', 'avatar_url', 'avatar', 'uploader_profile_image']) or
                          try_get(item, [('author','avatarLarger'), ('author','avatarThumb'), ('author','avatarMedium')]) or '')
            follower_count = (item.get('uploader_follower_count') or
                              try_get(item, [('authorStats','followerCount')]) or 0)
            following_count = (item.get('uploader_following_count') or
                               try_get(item, [('authorStats','followingCount')]) or 0)
            
            profile_data = {
                'display_name': display_name or username,
                'profile_picture': avatar_url or '',
                'follower_count': int(follower_count or 0),
                'following_count': int(following_count or 0),
'video_count': len([i for i in flat_meta if isinstance(i, dict) and 'url' in i])
            }
            break
    
    # Fallback when nothing parsed
    if not profile_data:
        profile_data = {
            'display_name': username,
            'profile_picture': '',
            'follower_count': 0,
            'following_count': 0,
            'video_count': len([i for i in flat_meta if isinstance(i, dict) and 'url' in i])
        }
    
    # Try to download and cache avatar using gallery-dl directly
    local_avatar = None
    try:
        local_avatar = download_avatar_with_gallery_dl(username, platform)
        if local_avatar:
            print(f"✅ Avatar successfully cached for {username}")
        else:
            print(f"⚠️ Avatar download failed for {username}, will use placeholder")
    except Exception as e:
        print(f"⚠️ Avatar download error for {username}: {e}")
    
    # Update database
    conn = get_db_connection()
    conn.execute('''
        UPDATE users SET
            display_name = ?,
            profile_picture = ?,
            follower_count = ?,
            following_count = ?,
            video_count = ?,
            last_sync = CURRENT_TIMESTAMP
        WHERE username = ? AND platform = ?
    ''', (
        profile_data.get('display_name', username),
        (os.path.basename(local_avatar) if local_avatar else ''),
        profile_data.get('follower_count', 0),
        profile_data.get('following_count', 0),
        profile_data.get('video_count', 0),
        username,
        platform
    ))
    conn.commit()
    conn.close()
    
    return True, "Stats updated successfully"

# --- Setup Routes ---
@app.route('/setup')
def setup_page():
    """Render the first-time setup page."""
    # If already setup, redirect to index (unless force param?)
    if get_bool_setting('setup_completed', False):
        return redirect(url_for('index'))
    return render_template('setup.html')

@app.route('/api/complete_setup', methods=['POST'])
def complete_setup():
    """Handle setup form submission."""
    try:
        bot_token = request.form.get('bot_token')
        chat_id = request.form.get('chat_id')
        import_file = request.files.get('import_file')

        if not bot_token or not chat_id:
            return jsonify({'success': False, 'error': 'Missing credentials'}), 400

        # Save credentials
        set_setting('telegram_bot_token', bot_token.strip())
        set_setting('telegram_chat_id', chat_id.strip())

        # Handle import if provided
        if import_file and import_file.filename.endswith('.zip'):
             # Reuse import logic conceptually or call function if refactored. 
             # For now, inline logic similar to import_database but simplified:
             filename = secure_filename(import_file.filename)
             filepath = os.path.join('data', filename) # Temp save
             import_file.save(filepath)
             
             try:
                 # We reuse the logic from import_database essentially:
                 # But we must close DB first? DB is per-request in Flask mostly.
                 conn = get_db_connection() # Just to be safe? No, let's close it if open.
                 conn.close()
                 
                 # Using the existing import_settings logic would be better if we could, 
                 # but we are in a route. Let's direct call the logic from import_settings 
                 # or essentially unzip and restore.
                 # Actually, let's just use the 'import_settings' route logic pattern:
                 with zipfile.ZipFile(filepath, 'r') as zf:
                    # 1. Restore DB
                    if 'trackui.db' in zf.namelist():
                        zf.extract('trackui.db', 'data')
                    
                    # 2. Restore JSON (settings) - careful not to overwrite our new token
                    # We should restore DB *then* re-apply our new token setting.
                    
                    # 3. Restore Config
                    if 'config.json' in zf.namelist():
                         zf.extract('config.json', 'data')

                 os.remove(filepath)
                 
                 # Re-apply tokens as import might have overwritten settings
                 set_setting('telegram_bot_token', bot_token.strip())
                 set_setting('telegram_chat_id', chat_id.strip())
                 
             except Exception as e:
                 return jsonify({'success': False, 'error': f'Import failed: {str(e)}'}), 500

        # Mark setup as complete
        set_setting('setup_completed', '1')
        
        # Initialize Bot immediately
        start_telegram_bot()
        
        # specific for "first time setup screen that asks for the bot credentials... also i would like that when you do a full reset it brings you to that screen"
        # The factory_reset function effectively clears settings, so `setup_completed` will be gone.
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"Setup error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/')
def index():
    """Main page showing tracked users."""
    # Check setup status
    if not get_bool_setting('setup_completed', False):
        return redirect(url_for('setup_page'))

    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 14, type=int)  # 14 items = 2 full rows
    tag_filter = request.args.get('tag', '')
    platform_filter = request.args.get('platform', '')
    search_query = request.args.get('search', '')
    
    conn = get_db_connection()
    
    # Get tags for filter dropdown
    tags = conn.execute('SELECT * FROM tags ORDER BY name').fetchall()
    
    # Build query with optional tag/platform filter
    base_query = '''
        SELECT DISTINCT u.*, COUNT(DISTINCT ut.tag_id) as tag_count
        FROM users u
        LEFT JOIN user_tags ut ON u.id = ut.user_id
    '''
    
    params = []
    where_clauses = []
    if tag_filter:
        base_query += '''
            INNER JOIN user_tags ut2 ON u.id = ut2.user_id
            INNER JOIN tags t ON ut2.tag_id = t.id
        '''
        where_clauses.append('t.name = ?')
        params.append(tag_filter)
    if platform_filter in ('tiktok','instagram', 'coomer'):
        where_clauses.append('u.platform = ?')
        params.append(platform_filter)
    if search_query:
        where_clauses.append('(u.username LIKE ? OR u.display_name LIKE ?)')
        params.extend([f'%{search_query}%', f'%{search_query}%'])
        
    if where_clauses:
        base_query += ' WHERE ' + ' AND '.join(where_clauses)
    
    base_query += ' GROUP BY u.id ORDER BY u.created_at DESC'
    
    # Get total count
    if where_clauses:
        count_query = 'SELECT COUNT(DISTINCT u.id) FROM users u '
        if tag_filter:
            count_query += 'INNER JOIN user_tags ut ON u.id = ut.user_id INNER JOIN tags t ON ut.tag_id = t.id '
        count_query += 'WHERE ' + ' AND '.join(where_clauses)
        total = conn.execute(count_query, params).fetchone()[0]
    else:
        total = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    
    # Get paginated results
    offset = (page - 1) * per_page
    paginated_query = f"{base_query} LIMIT ? OFFSET ?"
    users = conn.execute(paginated_query, params + [per_page, offset]).fetchall()
    
    conn.close()
    
    # Add download counts and user tags
    user_list = []
    for user in users:
        user_dict = dict(user)
        
        # Count downloaded files
        platform = user.get('platform','tiktok') if isinstance(user, dict) else user['platform']
        user_dir = os.path.join(DOWNLOADS_PATH, platform, user['username'])
        file_count = 0
        if os.path.exists(user_dir):
            for root, dirs, files in os.walk(user_dir):
                file_count += len([f for f in files if f.lower().endswith(('.mp4', '.jpg', '.jpeg', '.png', '.gif'))])
        
        user_dict['downloaded_files'] = file_count
        
        # Avatar availability
        avatar_available = False
        for ext in ['.jpg', '.jpeg', '.png', '.webp']:
            candidate = os.path.join(AVATARS_PATH, f"{user['platform']}_{user['username']}{ext}")
            if os.path.exists(candidate):
                avatar_available = True
                break
        user_dict['avatar_available'] = avatar_available
        
        # Get user tags
        conn = get_db_connection()
        user_tags = conn.execute('''
            SELECT t.name, t.color FROM tags t
            JOIN user_tags ut ON t.id = ut.tag_id
            WHERE ut.user_id = ?
        ''', (user['id'],)).fetchall()
        conn.close()
        
        user_dict['tags'] = [dict(tag) for tag in user_tags]
        user_list.append(user_dict)
    
    # Pagination info
    has_prev = page > 1
    has_next = offset + per_page < total
    prev_num = page - 1 if has_prev else None
    next_num = page + 1 if has_next else None
    
    return render_template('index.html',
                         users=user_list,
                         tags=tags,
                         current_tag=tag_filter,
                         pagination={
                             'page': page,
                             'per_page': per_page,
                             'total': total,
                             'has_prev': has_prev,
                             'has_next': has_next,
                             'prev_num': prev_num,
                             'next_num': next_num
                         },
                         sync_status=sync_status)

@app.route('/user/<username>')
def user_profile(username):
    """User profile page showing downloaded content."""
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
    
    if not user:
        conn.close()
        abort(404)
    
    # Get user tags
    user_tags = conn.execute('''
        SELECT t.* FROM tags t
        JOIN user_tags ut ON t.id = ut.tag_id
        WHERE ut.user_id = ?
    ''', (user['id'],)).fetchall()
    
    conn.close()
    
    # Get the user's actual platform from the database
    user_platform = user['platform'] if 'platform' in user.keys() else 'tiktok'
    # Allow URL parameter to override for backward compatibility, but prefer database value
    platform = user_platform if user_platform else request.args.get('platform', 'tiktok')
    user_dir = os.path.join(DOWNLOADS_PATH, platform, username)
    media_files = []
    
    if os.path.exists(user_dir):
        for root, dirs, files in os.walk(user_dir):
            for file in files:
                if file.lower().endswith(('.mp4', '.jpg', '.jpeg', '.png', '.gif')):
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, DOWNLOADS_PATH)
                    file_type = 'video' if file.lower().endswith('.mp4') else 'image'
                    
                    media_files.append({
                        'filename': file,
                        'path': rel_path.replace('\\', '/'),  # Ensure forward slashes for URLs
                        'type': file_type,
                        'size': os.path.getsize(file_path),
                        'modified': datetime.fromtimestamp(os.path.getmtime(file_path))
                    })
    
    # Apply videos-only filter if enabled
    videos_only = get_bool_setting('profile_feed_videos_only', False)
    if videos_only:
        media_files = [m for m in media_files if m['type'] == 'video']
    
    # Sort files - videos first, then images, then by date
    media_files.sort(key=lambda x: (x['type'] != 'video', -x['modified'].timestamp()))
    
    # For Instagram users, also get stories and highlights
    stories_files = []
    highlights_files = []
    if platform == 'instagram':
        # Get stories
        stories_dir = os.path.join(DOWNLOADS_PATH, platform, username, 'stories')
        if os.path.exists(stories_dir):
            for root, dirs, files in os.walk(stories_dir):
                for file in files:
                    if file.lower().endswith(('.mp4', '.jpg', '.jpeg', '.png', '.gif')):
                        file_path = os.path.join(root, file)
                        rel_path = os.path.relpath(file_path, DOWNLOADS_PATH)
                        file_type = 'video' if file.lower().endswith('.mp4') else 'image'
                        
                        stories_files.append({
                            'filename': file,
                            'path': rel_path.replace('\\', '/'),
                            'type': file_type,
                            'size': os.path.getsize(file_path),
                            'modified': datetime.fromtimestamp(os.path.getmtime(file_path))
                        })
        
        # Get highlights
        highlights_dir = os.path.join(DOWNLOADS_PATH, platform, username, 'highlights')
        if os.path.exists(highlights_dir):
            for root, dirs, files in os.walk(highlights_dir):
                for file in files:
                    if file.lower().endswith(('.mp4', '.jpg', '.jpeg', '.png', '.gif')):
                        file_path = os.path.join(root, file)
                        rel_path = os.path.relpath(file_path, DOWNLOADS_PATH)
                        file_type = 'video' if file.lower().endswith('.mp4') else 'image'
                        
                        highlights_files.append({
                            'filename': file,
                            'path': rel_path.replace('\\', '/'),
                            'type': file_type,
                            'size': os.path.getsize(file_path),
                            'modified': datetime.fromtimestamp(os.path.getmtime(file_path))
                        })
        
        # Sort stories and highlights by date
        stories_files.sort(key=lambda x: -x['modified'].timestamp())
        highlights_files.sort(key=lambda x: -x['modified'].timestamp())
        
        # Group highlights by collection using JSON metadata
        highlights_by_folder = {}
        import json
        
        for file in highlights_files:
            folder_name = 'General'
            
            # Try to read JSON metadata to get highlight collection name
            json_path = os.path.join(DOWNLOADS_PATH, file['path'] + '.json')
            if os.path.exists(json_path):
                try:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                        if 'highlight_title' in metadata and metadata['highlight_title']:
                            folder_name = metadata['highlight_title'].strip()
                            print(f"Found highlight collection '{folder_name}' for {file['filename']}")
                        else:
                            print(f"No highlight_title found in metadata for {file['filename']}")
                except Exception as e:
                    print(f"Error reading JSON metadata for {file['filename']}: {e}")
            else:
                print(f"No JSON metadata found for {file['filename']} at {json_path}")
            
            # Fallback: try to extract from path structure
            if folder_name == 'General':
                path_parts = file['path'].split('/')
                for i, part in enumerate(path_parts):
                    if 'highlights' in part.lower():
                        if i + 1 < len(path_parts) - 1:
                            folder_name = path_parts[i + 1]
                            break
            
            print(f"Grouping highlight file {file['filename']} into collection: {folder_name}")
            
            if folder_name not in highlights_by_folder:
                highlights_by_folder[folder_name] = []
            highlights_by_folder[folder_name].append(file)
        
        # Convert to list of tuples for template, sort by name
        highlights_grouped = sorted([(folder, files) for folder, files in highlights_by_folder.items()])
        
        print(f"\n=== HIGHLIGHTS GROUPING DEBUG ===")
        print(f"Total highlights files: {len(highlights_files)}")
        print(f"Number of highlight collections: {len(highlights_grouped)}")
        for folder_name, files in highlights_grouped:
            print(f"  Collection '{folder_name}': {len(files)} files")
            for f in files[:2]:  # Show first 2 files
                print(f"    - {f['filename']}")
        print(f"=====================================\n")
    
    # Compute counts and avatar url
    videos_count = len([m for m in media_files if m['type'] == 'video'])
    images_count = len([m for m in media_files if m['type'] == 'image'])
    stories_count = len(stories_files)
    highlights_count = len(highlights_files)
    
    avatar_url = None
    # Check cached avatar (try platform-prefixed format first, then legacy format)
    for ext in ['.jpg', '.jpeg', '.png', '.webp']:
        # Try new format with platform prefix first
        candidate = os.path.join(AVATARS_PATH, f"{platform}_{username}{ext}")
        if os.path.exists(candidate):
            avatar_url = f"/avatar/{username}"
            break
        # Try legacy format without platform prefix for backward compatibility
        candidate_legacy = os.path.join(AVATARS_PATH, f"{username}{ext}")
        if os.path.exists(candidate_legacy):
            avatar_url = f"/avatar/{username}"
            break
    
    # Fallback to first image
    if not avatar_url:
        first_image = next((m for m in media_files if m['type'] == 'image'), None)
        if first_image:
            avatar_url = f"/downloads/{first_image['path']}"
    
    return render_template('user.html',
                         user=dict(user),
                         user_tags=[dict(tag) for tag in user_tags],
                         media_files=media_files,
                         stories_files=stories_files,
                         highlights_files=highlights_files,
                         highlights_grouped=highlights_grouped if platform == 'instagram' else [],
                         media_counts={
                             'videos': videos_count, 
                             'images': images_count, 
                             'stories': stories_count,
                             'highlights': highlights_count,
                             'total': len(media_files)
                         },
                         avatar_url=avatar_url,
                         download_progress=download_progress.get(username, {}),
                         platform=platform)

# API Routes



def run_gallery_dl_json_coomer(username, retry_count=0):
    """Extract metadata for Coomer.su profile using gallery-dl."""
    try:
        url = f"https://coomer.su/onlyfans/user/{username}"
        # Note: Coomer support might vary, using generic URL pattern. 
        # If username assumes a specific service on coomer, we might need more inputs.
        # For now, assuming standard coomer profile URL structure if possible, 
        # but coomer URLs are often /service/user/username.
        # Let's try the generic search or profile pattern if gallery-dl supports it.
        # Actually, gallery-dl handles https://coomer.su/service/user/id usually.
        # Since input is just 'username', we might need to know the service (onlyfans, patreon etc).
        # However, for simplicity/MVP, let's assume the user might provide just username
        # and we default to a search or maybe the user needs to provide full URL?
        # But this app takes 'username' and 'platform'.
        # Let's assume the user inputs 'service/username' or we try a common one.
        # BETTER APPROACH: Coomer users on this app are likely imported or simple strings.
        # gallery-dl suggests: https://coomer.su/artist/{username} could work? No.
        # Let's stick to a generic URL query or rely on gallery-dl to handle "coomer:{username}" if that works?
        # No, gallery-dl needs a URL.
        # The user request just says "coomer.su platform".
        # Most common use is likely OnlyFans. Let's try to construct a valid URL for gallery-dl
        # or just pass the simple URL if gallery-dl supports it.
        # Let's update `run_gallery_dl_download` to handle the URL construction.
        
        # ACTUALLY: gallery-dl supports "coomer" extractor.
        # Let's try constructing a URL that covers most cases or search.
        # OnlyFans is the most popular, so `https://coomer.su/onlyfans/user/{username}`.
        url = f"https://coomer.su/onlyfans/user/{username}"
        
        cmd = ['gallery-dl', '--dump-json', '--no-download', url]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_THRESHOLD)
        if result.returncode != 0:
            # Try patreon as fallback?
            url_patreon = f"https://coomer.su/patreon/user/{username}"
            cmd = ['gallery-dl', '--dump-json', '--no-download', url_patreon]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_THRESHOLD)
            
            if result.returncode != 0:
                return None, f"gallery-dl error: {result.stderr}"
                
        out = result.stdout.strip()
        if not out:
            return [], None
        
        metadata = []
        try:
            data = json.loads(out)
            if isinstance(data, list):
                metadata.extend(data)
            else:
                metadata.append(data)
        except json.JSONDecodeError:
            for line in out.split('\n'):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    metadata.append(data)
                except json.JSONDecodeError:
                    continue
        return metadata, None
    except Exception as e:
        return None, f"Error: {str(e)}"

@app.route('/api/add_user', methods=['POST'])
def add_user():
    """Add a new user for tracking (TikTok, Instagram, or Coomer)."""
    data = request.get_json()
    username = data.get('username', '').strip().replace('@', '')
    platform = (data.get('platform') or 'tiktok').strip().lower()
    if platform not in ('tiktok','instagram', 'coomer'):
        platform = 'tiktok'
    
    if not username:
        return jsonify({'success': False, 'error': 'Username is required'})
    
    conn = get_db_connection()
    
    # Check if user already exists (for this platform)
    existing = conn.execute('SELECT id FROM users WHERE username = ? AND platform = ?', (username, platform)).fetchone()
    if existing:
        conn.close()
        return jsonify({'success': False, 'error': 'User already exists for this platform'})
    
    try:
        # Insert new user
        conn.execute('''
            INSERT INTO users (username, platform, display_name, is_tracking)
            VALUES (?, ?, ?, 1)
        ''', (username, platform, username))
        conn.commit()
        
        # Try to get initial stats (run in background to avoid blocking)
        import threading
        def sync_new_user():
            success, message = update_user_stats(username, platform)
            if message and isinstance(message, str) and "timed out" in message.lower():
                print(f"Initial sync for {username} ({platform}): ⏱️ {message} - Consider using manual sync later")
            else:
                print(f"Initial sync for {username} ({platform}): {message}")
        
        threading.Thread(target=sync_new_user).start()
        
        conn.close()
        return jsonify({'success': True, 'message': 'User added successfully'})
        
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/remove_user/<username>', methods=['DELETE'])
def remove_user(username):
    """Remove user and optionally delete downloaded files."""
    delete_files = request.args.get('delete_files', 'false').lower() == 'true'
    platform = request.args.get('platform', '').lower()
    
    if not platform or platform not in ('tiktok', 'instagram', 'coomer'):
        return jsonify({'success': False, 'error': 'Platform parameter is required (tiktok, instagram, or coomer)'})
    
    conn = get_db_connection()
    
    # Get user ID for tag cleanup - filter by both username AND platform
    user = conn.execute('SELECT id, platform FROM users WHERE username = ? AND platform = ?', (username, platform)).fetchone()
    if not user:
        conn.close()
        return jsonify({'success': False, 'error': 'User not found'})
    
    try:
        # Remove user tags
        conn.execute('DELETE FROM user_tags WHERE user_id = ?', (user['id'],))
        
        # Remove user - filter by both username AND platform
        conn.execute('DELETE FROM users WHERE username = ? AND platform = ?', (username, platform))
        conn.commit()
        conn.close()
        
        # Delete files if requested - use platform-specific path
        if delete_files:
            user_dir = os.path.join(DOWNLOADS_PATH, platform, username)
            if os.path.exists(user_dir):
                shutil.rmtree(user_dir)
            # Also clean up avatar files
            for ext in ['.jpg', '.jpeg', '.png', '.webp']:
                avatar_file = os.path.join(AVATARS_PATH, f"{platform}_{username}{ext}")
                if os.path.exists(avatar_file):
                    os.remove(avatar_file)
        
        return jsonify({'success': True, 'message': 'User removed successfully'})
        
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/toggle_tracking/<username>', methods=['POST'])
def toggle_tracking(username):
    """Toggle tracking status for a user."""
    data = request.get_json(force=True, silent=True) or {}
    platform = data.get('platform', '').lower()
    
    if not platform or platform not in ('tiktok', 'instagram'):
        return jsonify({'success': False, 'error': 'Platform parameter is required (tiktok or instagram)'})
    
    conn = get_db_connection()
    
    try:
        user = conn.execute('SELECT is_tracking FROM users WHERE username = ? AND platform = ?', (username, platform)).fetchone()
        if not user:
            conn.close()
            return jsonify({'success': False, 'error': 'User not found'})
        
        new_status = not user['is_tracking']
        conn.execute('UPDATE users SET is_tracking = ? WHERE username = ? AND platform = ?', (new_status, username, platform))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'tracking': new_status})
        
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/settings/export')
def export_settings():
    """Export database (users, tags, settings) and cookie files to a zip."""
    import zipfile
    import io
    from flask import send_file

    try:
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            conn = get_db_connection()
            
            # 1. Users
            users = conn.execute('SELECT * FROM users').fetchall()
            users_list = [dict(u) for u in users]
            zf.writestr('users.json', json.dumps(users_list, indent=2))
            
            # 2. Tags
            tags = conn.execute('SELECT * FROM tags').fetchall()
            tags_list = [dict(t) for t in tags]
            zf.writestr('tags.json', json.dumps(tags_list, indent=2))

            # 3. User Tags
            user_tags = conn.execute('SELECT * FROM user_tags').fetchall()
            user_tags_list = [dict(ut) for ut in user_tags]
            zf.writestr('user_tags.json', json.dumps(user_tags_list, indent=2))
            
            # 4. Settings
            settings = conn.execute('SELECT * FROM settings').fetchall()
            settings_dict = {row['key']: row['value'] for row in settings}
            zf.writestr('settings.json', json.dumps(settings_dict, indent=2))
            
            # 5. Cookie Files
            # Look for tracked cookies in settings
            cookie_keys = ['instagram_active_cookies', 'instagram_following_cookies']
            for key in cookie_keys:
                filename = settings_dict.get(key)
                if filename:
                    if os.path.exists(filename):
                        zf.write(filename, f"cookies/{filename}")
                    elif os.path.exists(os.path.join('data', filename)):
                        zf.write(os.path.join('data', filename), f"cookies/{filename}")

            conn.close()
            
            # Check for standard cookies.txt in root
            if os.path.exists('cookies.txt'):
                 zf.write('cookies.txt', 'cookies/cookies.txt')

        memory_file.seek(0)
        return send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f'trackui_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip'
        )

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/import', methods=['POST'])
def import_settings():
    """Import users and cookies from a zip file."""
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'})
    
    file = request.files['file']
    if not file.filename.endswith('.zip'):
        return jsonify({'success': False, 'error': 'Invalid file type. Please upload a ZIP file.'})

    try:
        import zipfile
        import io
        
        # Helper to get DB connection inside
        conn = get_db_connection()
        
        with zipfile.ZipFile(file) as zf:
            # 1. Restore Users
            if 'users.json' in zf.namelist():
                users_data = json.loads(zf.read('users.json'))
                count = 0
                for u in users_data:
                    # Upsert user
                    conn.execute('''
                        INSERT INTO users (username, platform, display_name, profile_picture, is_tracking, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(username, platform) DO UPDATE SET
                        is_tracking=excluded.is_tracking,
                        display_name=excluded.display_name
                    ''', (
                        u.get('username'), 
                        u.get('platform', 'tiktok'),
                        u.get('display_name'), 
                        u.get('profile_picture'), 
                        u.get('is_tracking', 1),
                        u.get('created_at')
                    ))
                    count += 1
                print(f"Imported/Updated {count} users")

            # 2. Restore Tags
            tag_map = {} # old_id -> new_id
            if 'tags.json' in zf.namelist():
                tags_data = json.loads(zf.read('tags.json'))
                for t in tags_data:
                    # check if tag name exists
                    existing = conn.execute('SELECT id FROM tags WHERE name = ?', (t['name'],)).fetchone()
                    if existing:
                        tag_map[t['id']] = existing['id']
                    else:
                        cur = conn.execute('INSERT INTO tags (name, color) VALUES (?, ?)', (t['name'], t['color']))
                        tag_map[t['id']] = cur.lastrowid

            # 3. Restore User Tags (with mapping)
            if 'user_tags.json' in zf.namelist() and 'users.json' in zf.namelist():
                 # Load all users into a map: (username, platform) -> id
                 all_users = conn.execute('SELECT id, username, platform FROM users').fetchall()
                 user_map = {(u['username'], u['platform']): u['id'] for u in all_users}
                 
                 user_tags_data = json.loads(zf.read('user_tags.json'))
                 # Map old user IDs to usernames using the export data
                 export_user_id_map = {u['id']: (u['username'], u.get('platform', 'tiktok')) for u in users_data}
                 
                 for ut in user_tags_data:
                     old_user_id = ut['user_id']
                     old_tag_id = ut['tag_id']
                     
                     if old_user_id in export_user_id_map and old_tag_id in tag_map:
                         username, platform = export_user_id_map[old_user_id]
                         new_user_id = user_map.get((username, platform))
                         new_tag_id = tag_map[old_tag_id]
                         
                         if new_user_id and new_tag_id:
                             conn.execute('INSERT OR IGNORE INTO user_tags (user_id, tag_id) VALUES (?, ?)', (new_user_id, new_tag_id))

            # 4. Restore Cookies
            for filename in zf.namelist():
                if filename.startswith('cookies/') and not filename.endswith('/'):
                    # Extract single file
                    cookie_content = zf.read(filename)
                    dest_filename = os.path.basename(filename)
                    # Write to root (assuming cookie files work from root)
                    with open(dest_filename, 'wb') as f:
                        f.write(cookie_content)
                    print(f"Restored cookie file: {dest_filename}")

            # 5. Restore Settings
            if 'settings.json' in zf.namelist():
                settings_data = json.loads(zf.read('settings.json'))
                for k, v in settings_data.items():
                    conn.execute('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (k, v))

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'Import completed successfully'})

    except Exception as e:
        print(f"Import error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings/factory-reset', methods=['POST'])
def factory_reset():
    """Reset the application to factory state."""
    data = request.get_json(force=True, silent=True) or {}
    delete_files = data.get('delete_files', False)
    
    try:
        conn = get_db_connection()
        
        # Disable foreign keys temporarily to truncate tables
        conn.execute('PRAGMA foreign_keys = OFF')
        
        # Clear all tables
        tables = ['users', 'tags', 'user_tags', 'likes', 'settings']
        for table in tables:
            conn.execute(f'DELETE FROM {table}')
            # Reset auto-increment counters
            conn.execute(f"DELETE FROM sqlite_sequence WHERE name='{table}'")
            
        conn.execute('PRAGMA foreign_keys = ON')
        conn.commit()
        conn.close()
        
        # Re-initialize default settings
        init_database()
        
        # Delete files if requested
        if delete_files:
            # Clear downloads
            if os.path.exists(DOWNLOADS_PATH):
                shutil.rmtree(DOWNLOADS_PATH)
                os.makedirs(DOWNLOADS_PATH, exist_ok=True)
                
            # Clear avatars
            if os.path.exists(AVATARS_PATH):
                shutil.rmtree(AVATARS_PATH)
                os.makedirs(AVATARS_PATH, exist_ok=True)
                
            # Clear logs
            global download_progress, global_download_queue, active_downloads, scheduler_logs
            download_progress = {}
            global_download_queue = []
            active_downloads = {}
            scheduler_logs = []
            
        return jsonify({'success': True, 'message': 'Factory reset completed successfully'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/settings', methods=['POST'])
def update_settings():
    """Update application settings."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400
            
        for key, value in data.items():
            # Convert boolean values to integers for storage
            if isinstance(value, bool):
                value = 1 if value else 0
            set_setting(key, str(value))
            
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error updating settings: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

def perform_download(username, reuse_existing=False, platform='tiktok'):
    """Perform a synchronous download for a user, updating queues and DB.
    Returns (success: bool, file_count: int).
    """
    # Add to global queue (unless we are resuming an existing entry)
    if not (reuse_existing and username in active_downloads):
        add_to_global_queue(username)
    
    download_progress[username] = {
        'status': 'downloading',
        'files_downloaded': 0,
        'current_file': '',
        'start_time': time.time(),
        'logs': []
    }
    
    # Update global queue
    update_global_queue(username, status='downloading')
    
    def progress_callback(file_count, current_file):
        download_progress[username].update({
            'files_downloaded': file_count,
            'current_file': current_file
        })
        # Update global queue
        update_global_queue(username, 
                          files_downloaded=file_count,
                          current_file=current_file)
    
    # Check granular settings
    sync_posts = get_bool_setting('sync_posts', True)
    sync_stories = get_bool_setting('sync_stories', True)
    sync_highlights = get_bool_setting('sync_highlights', True)
    
    success = True
    file_count = 0
    output = ''
    
    # Platform-specific "posts" download
    if sync_posts:
        success, output, file_count, paused = run_gallery_dl_download(username, progress_callback, platform)
        
        if paused:
            download_progress[username].update({
                'status': 'paused',
                'total_files': file_count,
                'end_time': time.time(),
                'logs': output.split('\n') if output else []
            })
            # Update global queue without removing from active list
            update_global_queue(username,
                              status='paused',
                              total_files=file_count,
                              logs=output.split('\n') if output else [])
            return False, file_count
    else:
        print(f"Skipping posts download for {username} (sync_posts=False)")
        update_global_queue(username, current_file="Skipping posts (disabled)")
        time.sleep(0.5)

    # Update database (even if paused, we persist counts so far)
    conn = get_db_connection()
    conn.execute('''
        UPDATE users SET 
            download_count = download_count + ?,
            last_download = CURRENT_TIMESTAMP
        WHERE username = ? AND platform = ?
    ''', (file_count, username, platform))
    conn.commit()
    conn.close()

    final_status = 'completed' if success else 'failed'
    download_progress[username].update({
        'status': final_status,
        'total_files': file_count,
        'end_time': time.time(),
        'logs': output.split('\n') if output else []
    })
    
    # Update global queue
    update_global_queue(username, 
                      status=final_status,
                      total_files=file_count,
                      logs=output.split('\n') if output else [])
    
    # Telegram Notification on Failure
    if not success:
        try:
            error_preview = "Unknown error"
            if output:
                # Get last 3 lines of output for context
                lines = output.strip().split('\n')
                error_preview = '\n'.join(lines[-3:])
            
            msg = (
                f"❌ *Download Failed*\n"
                f"👤 User: `{username}`\n"
                f"📱 Platform: {platform.title()}\n"
                f"⚠️ Error:\n`{error_preview}`"
            )
            send_telegram_message(msg)
        except Exception as e:
            print(f"Failed to send failure notification: {e}")
    
    # For Instagram users, automatically download stories and highlights if enabled
    if platform == 'instagram' and success:
        try:
            # Download stories (if enabled)
            if sync_stories:
                update_global_queue(username, current_file=f'Downloading @{username} stories...')
                stories_success, stories_output, stories_count = perform_download_instagram_aux(username, kind='stories')
                if stories_success and stories_count > 0:
                    update_global_queue(username, current_file=f'Stories completed: {stories_count} files')
                    print(f"Stories downloaded for {username}: {stories_count} files")
                elif stories_count == 0:
                    print(f"No new stories found for {username}")
                else:
                    print(f"Stories download failed for {username}: {stories_output[:100]}...")
            else:
                print(f"Skipping stories for {username} (sync_stories=False)")
            
            # Download highlights (if enabled)
            if sync_highlights:
                update_global_queue(username, current_file=f'Downloading @{username} highlights...')
                highlights_success, highlights_output, highlights_count = perform_download_instagram_aux(username, kind='highlights')
                if highlights_success and highlights_count > 0:
                    update_global_queue(username, current_file=f'Highlights completed: {highlights_count} files')
                    print(f"Highlights downloaded for {username}: {highlights_count} files")
                elif highlights_count == 0:
                    print(f"No new highlights found for {username}")
                else:
                    print(f"Highlights download failed for {username}: {highlights_output[:100]}...")
            else:
                 print(f"Skipping highlights for {username} (sync_highlights=False)")
            
        except Exception as e:
            print(f"Error downloading stories/highlights for {username}: {e}")
            update_global_queue(username, current_file=f'Stories/highlights error: {str(e)}')
    
    return success, file_count

@app.route('/api/download_user/<username>', methods=['POST'])
def download_user_content(username):
    """Start downloading content for a user."""
    # Get platform from database instead of request args for consistency
    conn = get_db_connection()
    user = conn.execute('SELECT platform FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()
    
    if not user:
        return jsonify({'success': False, 'error': 'User not found'})
    
    platform = user['platform']
    
    if username in download_progress and download_progress[username].get('status') == 'downloading':
        return jsonify({'success': False, 'error': 'Download already in progress'})
    
    def download_thread():
        perform_download(username, platform=platform)
    
    thread = threading.Thread(target=download_thread)
    thread.start()
    
    return jsonify({'success': True, 'message': f'Download started for {username} ({platform}) - including stories and highlights for Instagram users'})

@app.route('/api/downloads/pause/<username>', methods=['POST'])
def pause_download(username):
    """Pause a running download for a user by terminating the process gracefully."""
    _download_controls.setdefault(username, {'pause': False})
    _download_controls[username]['pause'] = True

    proc = download_processes.get(username)
    if proc and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
    # Update queues immediately
    update_global_queue(username, status='paused')
    if username in download_progress:
        download_progress[username]['status'] = 'paused'
    return jsonify({'success': True, 'message': 'Pause requested'})

@app.route('/api/downloads/resume/<username>', methods=['POST'])
def resume_download(username):
    """Resume a paused (or stopped) download for a user."""
    # Get platform from database
    conn = get_db_connection()
    user = conn.execute('SELECT platform FROM users WHERE username = ?', (username,)).fetchone()
    conn.close()
    
    if not user:
        return jsonify({'success': False, 'error': 'User not found'})
    
    platform = user['platform']
    
    _download_controls.setdefault(username, {'pause': False})
    _download_controls[username]['pause'] = False

    def resume_thread():
        # Reuse existing queue entry if present
        perform_download(username, reuse_existing=True, platform=platform)
    threading.Thread(target=resume_thread).start()
    return jsonify({'success': True, 'message': 'Resume started'})

@app.route('/api/download_progress/<username>')
def get_download_progress(username):
    """Get download progress for a user."""
    progress = download_progress.get(username, {})
    return jsonify(progress)

@app.route('/api/downloads/status')
def get_downloads_status():
    """Get global download status."""
    return jsonify(get_global_download_status())

@app.route('/api/downloads/clear_completed', methods=['POST'])
def clear_completed_downloads():
    """Clear completed downloads from the queue (excluding sync operations)."""
    global global_download_queue
    # Keep sync operations and non-completed downloads
    global_download_queue = [d for d in global_download_queue 
                           if d['status'] not in ['completed', 'failed'] or d['username'] == SYNC_QUEUE_USERNAME]
    return jsonify({'success': True, 'message': 'Completed downloads cleared'})

@app.route('/api/downloads/instagram/stories/<username>', methods=['POST'])
def download_instagram_stories(username):
    """Download Instagram stories for a specific user."""
    try:
        # Check if user exists and is Instagram user
        conn = get_db_connection()
        user = conn.execute('SELECT platform FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        
        if not user:
            return jsonify({'success': False, 'error': 'User not found'})
        
        if user['platform'] != 'instagram':
            return jsonify({'success': False, 'error': 'Stories are only available for Instagram users'})
        
        # Create download ID
        download_id = f"{username}_stories_{int(time.time())}"
        
        def download_thread():
            update_global_queue(download_id, status='downloading', current_file=f'Downloading @{username} stories')
            try:
                success, output, file_count = perform_download_instagram_aux(username, kind='stories')
                if success:
                    update_global_queue(download_id, status='completed', 
                                      current_file=f'Stories download completed: {file_count} files',
                                      files_downloaded=file_count, total_files=file_count,
                                      logs=output.split('\n') if output else [])
                else:
                    update_global_queue(download_id, status='failed', 
                                      current_file=f'Stories download failed',
                                      logs=output.split('\n') if output else ['No output available'])
            except Exception as e:
                update_global_queue(download_id, status='failed', 
                                  current_file=f'Stories download failed: {str(e)}',
                                  logs=[str(e)])
        
        # Add to queue
        add_to_global_queue(username, download_id)
        
        # Start download in background
        thread = threading.Thread(target=download_thread)
        thread.start()
        
        return jsonify({'success': True, 'message': 'Stories download started', 'download_id': download_id})
        
    except Exception as e:
        return jsonify({'success': False, 'error': f'Failed to start stories download: {str(e)}'})

@app.route('/api/downloads/instagram/highlights/<username>', methods=['POST'])
def download_instagram_highlights(username):
    """Download Instagram highlights for a specific user."""
    try:
        # Check if user exists and is Instagram user
        conn = get_db_connection()
        user = conn.execute('SELECT platform FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        
        if not user:
            return jsonify({'success': False, 'error': 'User not found'})
        
        if user['platform'] != 'instagram':
            return jsonify({'success': False, 'error': 'Highlights are only available for Instagram users'})
        
        # Create download ID
        download_id = f"{username}_highlights_{int(time.time())}"
        
        def download_thread():
            update_global_queue(download_id, status='downloading', current_file=f'Downloading @{username} highlights')
            try:
                success, output, file_count = perform_download_instagram_aux(username, kind='highlights')
                if success:
                    update_global_queue(download_id, status='completed', 
                                      current_file=f'Highlights download completed: {file_count} files',
                                      files_downloaded=file_count, total_files=file_count,
                                      logs=output.split('\n') if output else [])
                else:
                    update_global_queue(download_id, status='failed', 
                                      current_file=f'Highlights download failed',
                                      logs=output.split('\n') if output else ['No output available'])
            except Exception as e:
                update_global_queue(download_id, status='failed', 
                                  current_file=f'Highlights download failed: {str(e)}',
                                  logs=[str(e)])
        
        # Add to queue
        add_to_global_queue(username, download_id)
        
        # Start download in background
        thread = threading.Thread(target=download_thread)
        thread.start()
        
        return jsonify({'success': True, 'message': 'Highlights download started', 'download_id': download_id})
        
    except Exception as e:
        return jsonify({'success': False, 'error': f'Failed to start highlights download: {str(e)}'})

def run_sync_all_process():
    """Internal: perform sync-all and per-user downloads, updating queues and status."""
    sync_status['running'] = True
    sync_status['last_sync'] = datetime.now()
    sync_status['timeout_users'] = []
    sync_status['current_timeout'] = False
    sync_logs.clear()
    
    # Notify Telegram (Start)
    send_telegram_message(f"📅 Scheduled Sync Started at {datetime.now().strftime('%H:%M')}")
    
    try:
        conn = get_db_connection()
        users = conn.execute('SELECT username, platform FROM users WHERE is_tracking = 1').fetchall()
        conn.close()
        
        total_users = len(users)
        processed = 0
        
        # Push a synthetic task into the Download Manager queue so progress shows up there
        add_to_global_queue(SYNC_QUEUE_USERNAME)
        update_global_queue(SYNC_QUEUE_USERNAME, status='downloading', total_files=total_users, files_downloaded=0, current_file='Preparing...')
        
        for user in users:
            username = user['username']
            platform = user.get('platform','tiktok') if isinstance(user, dict) else user['platform']
            sync_status['current_user'] = f"{username} ({platform})"
            sync_status['current_timeout'] = False  # Reset for each user
            sync_logs.append(f"Syncing {username} ({platform})...")
            
            # Update the Download Manager entry to reflect current user being synced
            update_global_queue(SYNC_QUEUE_USERNAME, current_file=f"Syncing @{username} [{platform}]")
            
            try:
                success, message = update_user_stats(username, platform)
                sync_logs.append(f"{username} ({platform}): {message}")
                
                if not success:
                    if message and "timed out" in message.lower():
                        sync_logs.append(f"⏱️ Timeout: {username} - {message}")
                    else:
                        sync_logs.append(f"Failed to sync {username}: {message}")
                else:
                    # Remove from timeout users if sync was successful
                    if username in sync_status.get('timeout_users', []):
                        sync_status['timeout_users'].remove(username)
                    
                    # After a successful metadata sync, download media for this user
                    update_global_queue(SYNC_QUEUE_USERNAME, current_file=f"Downloading @{username} [{platform}]")
                    try:
                        perform_download(username, platform=platform, reuse_existing=True)
                    except Exception as e:
                        sync_logs.append(f"Download error for {username} ({platform}): {e}")
                        
                    # For Instagram/Coomer already handled in perform_download (granular sync)
            except Exception as e:
                sync_logs.append(f"Critical error syncing {username}: {str(e)}")
                print(f"Error syncing {username}: {e}")
            
            processed += 1
            update_global_queue(SYNC_QUEUE_USERNAME, files_downloaded=processed, total_files=total_users)
            
            # Add delay between requests for rate limiting
            time.sleep(REQUEST_DELAY)
        
        # Mark the synthetic task as completed and attach last logs
        update_global_queue(SYNC_QUEUE_USERNAME, status='completed', logs=sync_logs[-50:])
        sync_logs.append("Sync completed")
        
        # Notify Telegram (End)
        send_telegram_message(f"✅ Sync Finished\nProcessed: {processed}/{total_users} users.")

    except Exception as e:
        print(f"Sync process crashed: {e}")
        sync_logs.append(f"Sync process crashed: {str(e)}")
        update_global_queue(SYNC_QUEUE_USERNAME, status='failed', logs=sync_logs[-50:])
        send_telegram_message(f"⚠️ Sync Process Crashed: {str(e)}")
        
    finally:
        sync_status['running'] = False
        sync_status['current_user'] = None

@app.route('/api/sync_all', methods=['POST'])
def sync_all_users():
    """Sync all tracked users and reflect progress in the Download Manager."""
    if sync_status['running']:
        return jsonify({'success': False, 'error': 'Sync already in progress'})
    t = threading.Thread(target=run_sync_all_process)
    t.start()
    return jsonify({'success': True, 'message': 'Sync started'})

@app.route('/api/sync_status')
def get_sync_status():
    """Get current sync status and logs."""
    return jsonify({
        'running': sync_status['running'],
        'current_user': sync_status['current_user'],
        'current_timeout': sync_status.get('current_timeout', False),
        'timeout_users': sync_status.get('timeout_users', []),
        'timeout_count': timeout_count,
        'last_sync': sync_status['last_sync'].isoformat() if sync_status['last_sync'] else None,
        'logs': sync_logs[-50:]  # Last 50 log entries
    })

# Feed routes

@app.route('/feed')
def feed():
    """TikTok-like scrolling feed with all downloaded media."""
    return render_template('feed.html')

@app.route('/api/feed/media')
def get_feed_media():
    """Get random mixed media from all downloaded profiles."""
    try:
        limit = request.args.get('limit', 20, type=int)
        offset = request.args.get('offset', 0, type=int)
        
        # Collect all media files from all users
        media_items = []
        
        conn = get_db_connection()
        users = conn.execute('SELECT username, platform, display_name FROM users').fetchall()
        conn.close()
        
        for user in users:
            username = user['username']
            platform = user['platform']
            display_name = user['display_name'] or username
            
            # Main downloads directory
            user_dir = os.path.join(DOWNLOADS_PATH, platform, username)
            
            if os.path.exists(user_dir):
                for root, dirs, files in os.walk(user_dir):
                    for file in files:
                        if file.lower().endswith(('.mp4', '.jpg', '.jpeg', '.png', '.gif')):
                            file_path = os.path.join(root, file)
                            rel_path = os.path.relpath(file_path, DOWNLOADS_PATH)
                            file_type = 'video' if file.lower().endswith('.mp4') else 'image'
                            
                            # Determine content type (post, story, highlight)
                            content_type = 'post'
                            if 'stories' in rel_path.lower():
                                content_type = 'story'
                            elif 'highlights' in rel_path.lower():
                                content_type = 'highlight'
                            
                            media_items.append({
                                'path': rel_path.replace('\\', '/'),
                                'type': file_type,
                                'username': username,
                                'display_name': display_name,
                                'platform': platform,
                                'content_type': content_type,
                                'size': os.path.getsize(file_path),
                                'modified': os.path.getmtime(file_path)
                            })
        
        # Smart shuffle to prevent consecutive posts from same user
        import random
        
        # Group media by user
        user_media = {}
        for item in media_items:
            username = item['username']
            if username not in user_media:
                user_media[username] = []
            user_media[username].append(item)
        
        # Shuffle each user's media
        for username in user_media:
            random.shuffle(user_media[username])
        
        # Interleave posts from different users
        shuffled_media = []
        user_indices = {username: 0 for username in user_media}
        last_username = None
        
        while len(shuffled_media) < len(media_items):
            # Get list of users who still have media
            available_users = [u for u in user_media if user_indices[u] < len(user_media[u])]
            
            if not available_users:
                break
            
            # Remove last used user from options if possible
            if last_username and last_username in available_users and len(available_users) > 1:
                available_users = [u for u in available_users if u != last_username]
            
            # Pick a random user from available
            username = random.choice(available_users)
            
            # Add one item from this user
            shuffled_media.append(user_media[username][user_indices[username]])
            user_indices[username] += 1
            last_username = username
        
        # Apply pagination
        paginated_items = shuffled_media[offset:offset + limit]
        
        return jsonify({
            'success': True,
            'media': paginated_items,
            'total': len(shuffled_media),
            'has_more': (offset + limit) < len(shuffled_media)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/feed/like', methods=['POST'])
def toggle_like():
    """Toggle like status for a media item."""
    try:
        data = request.get_json()
        media_path = data.get('media_path', '').strip()
        
        if not media_path:
            return jsonify({'success': False, 'error': 'Media path is required'})
        
        conn = get_db_connection()
        
        # Check if already liked
        existing = conn.execute('SELECT id FROM likes WHERE media_path = ?', (media_path,)).fetchone()
        
        if existing:
            # Unlike
            conn.execute('DELETE FROM likes WHERE media_path = ?', (media_path,))
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'liked': False, 'message': 'Unliked'})
        else:
            # Like
            conn.execute('INSERT INTO likes (media_path) VALUES (?)', (media_path,))
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'liked': True, 'message': 'Liked'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/feed/likes')
def get_likes():
    """Get all liked media paths."""
    try:
        conn = get_db_connection()
        likes = conn.execute('SELECT media_path FROM likes').fetchall()
        conn.close()
        
        liked_paths = [like['media_path'] for like in likes]
        return jsonify({'success': True, 'likes': liked_paths})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# Tag management routes

@app.route('/api/tags', methods=['GET', 'POST'])
def manage_tags():
    """Get all tags or create a new tag."""
    if request.method == 'GET':
        try:
            conn = get_db_connection()
            tags = conn.execute('SELECT * FROM tags ORDER BY name').fetchall()
            conn.close()
            return jsonify({'success': True, 'tags': [dict(tag) for tag in tags]})
        except Exception as e:
            return jsonify({'success': False, 'error': f'Failed to get tags: {str(e)}'})
    
    elif request.method == 'POST':
        try:
            data = request.get_json()
            if not data:
                return jsonify({'success': False, 'error': 'No data provided'})
            
            name = data.get('name', '').strip()
            color = data.get('color', '#007bff').strip()
            
            if not name:
                return jsonify({'success': False, 'error': 'Tag name is required'})
            
            if len(name) > 50:
                return jsonify({'success': False, 'error': 'Tag name too long (max 50 characters)'})
            
            conn = get_db_connection()
            
            # Check if tag already exists
            existing = conn.execute('SELECT id FROM tags WHERE LOWER(name) = LOWER(?)', (name,)).fetchone()
            if existing:
                conn.close()
                return jsonify({'success': False, 'error': 'Tag with this name already exists'})
            
            # Create new tag
            cursor = conn.cursor()
            cursor.execute('INSERT INTO tags (name, color) VALUES (?, ?)', (name, color))
            tag_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            return jsonify({
                'success': True, 
                'message': 'Tag created successfully',
                'tag': {'id': tag_id, 'name': name, 'color': color}
            })
            
        except Exception as e:
            return jsonify({'success': False, 'error': f'Failed to create tag: {str(e)}'})

@app.route('/api/tags/<int:tag_id>', methods=['PUT', 'DELETE'])
def modify_tag(tag_id):
    """Update or delete a tag."""
    try:
        conn = get_db_connection()
        
        # Check if tag exists
        tag = conn.execute('SELECT * FROM tags WHERE id = ?', (tag_id,)).fetchone()
        if not tag:
            conn.close()
            return jsonify({'success': False, 'error': 'Tag not found'})
        
        if request.method == 'DELETE':
            # Remove tag associations first
            conn.execute('DELETE FROM user_tags WHERE tag_id = ?', (tag_id,))
            # Remove the tag
            conn.execute('DELETE FROM tags WHERE id = ?', (tag_id,))
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'message': 'Tag deleted successfully'})
        
        elif request.method == 'PUT':
            data = request.get_json()
            if not data:
                conn.close()
                return jsonify({'success': False, 'error': 'No data provided'})
            
            name = data.get('name', '').strip()
            color = data.get('color', '').strip()
            
            if not name or not color:
                conn.close()
                return jsonify({'success': False, 'error': 'Name and color are required'})
            
            # Update tag
            conn.execute('UPDATE tags SET name = ?, color = ? WHERE id = ?', (name, color, tag_id))
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'message': 'Tag updated successfully'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': f'Failed to modify tag: {str(e)}'})

@app.route('/api/users/<username>/tags', methods=['GET', 'POST', 'PUT'])
def manage_user_tags(username):
    """Get user tags or update user's tags."""
    try:
        conn = get_db_connection()
        
        # Get user ID
        user = conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
        if not user:
            conn.close()
            return jsonify({'success': False, 'error': 'User not found'})
        
        user_id = user['id']
        
        if request.method == 'GET':
            # Return user's current tags
            user_tags = conn.execute('''
                SELECT t.id, t.name, t.color, t.created_at FROM tags t
                JOIN user_tags ut ON t.id = ut.tag_id
                WHERE ut.user_id = ?
                ORDER BY t.name
            ''', (user_id,)).fetchall()
            conn.close()
            return jsonify({'success': True, 'tags': [dict(tag) for tag in user_tags]})
        
        elif request.method == 'POST':
            # Add a tag to user
            data = request.get_json()
            tag_id = data.get('tag_id')
            
            if not tag_id:
                conn.close()
                return jsonify({'success': False, 'error': 'Tag ID is required'})
            
            # Check if tag exists
            tag_exists = conn.execute('SELECT id FROM tags WHERE id = ?', (tag_id,)).fetchone()
            if not tag_exists:
                conn.close()
                return jsonify({'success': False, 'error': 'Tag does not exist'})
            
            # Add tag to user (ignore if already exists)
            conn.execute('INSERT OR IGNORE INTO user_tags (user_id, tag_id) VALUES (?, ?)', 
                        (user_id, tag_id))
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'message': 'Tag added to user'})
        
        elif request.method == 'PUT':
            # Replace all user tags
            data = request.get_json()
            tag_ids = data.get('tag_ids', [])
            
            if not isinstance(tag_ids, list):
                conn.close()
                return jsonify({'success': False, 'error': 'tag_ids must be a list'})
            
            # Remove all existing tags for user
            conn.execute('DELETE FROM user_tags WHERE user_id = ?', (user_id,))
            
            # Add new tags
            for tag_id in tag_ids:
                # Verify tag exists
                tag_exists = conn.execute('SELECT id FROM tags WHERE id = ?', (tag_id,)).fetchone()
                if tag_exists:
                    conn.execute('INSERT OR IGNORE INTO user_tags (user_id, tag_id) VALUES (?, ?)', 
                                (user_id, tag_id))
            
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'message': 'User tags updated successfully'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': f'Failed to manage user tags: {str(e)}'})

@app.route('/api/users/<username>/tags/<int:tag_id>', methods=['DELETE'])
def remove_user_tag(username, tag_id):
    """Remove a specific tag from a user."""
    try:
        conn = get_db_connection()
        
        # Get user ID
        user = conn.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
        if not user:
            conn.close()
            return jsonify({'success': False, 'error': 'User not found'})
        
        # Remove the tag from user
        result = conn.execute('DELETE FROM user_tags WHERE user_id = ? AND tag_id = ?', 
                            (user['id'], tag_id))
        
        if result.rowcount == 0:
            conn.close()
            return jsonify({'success': False, 'error': 'Tag not assigned to user'})
        
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'Tag removed from user'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': f'Failed to remove tag: {str(e)}'})

@app.route('/api/test_access')
def test_access():
    """Test TikTok and Instagram access and gallery-dl availability."""
    t_success, t_msg = test_tiktok_access()
    # Instagram test
    try:
        ig_cmd = ['gallery-dl', '--version']
        subprocess.run(ig_cmd, capture_output=True, text=True, timeout=10)
        active = get_setting('instagram_active_cookies','') or ''
        cookie_path = os.path.join('data','cookies','instagram', active) if active else ''
        cmd = ['gallery-dl', '--dump-json', '--no-download']
        if active and os.path.exists(cookie_path):
            cmd.extend(['--cookies', cookie_path])
        ig_result = subprocess.run(cmd + ['https://www.instagram.com/'], capture_output=True, text=True, timeout=30)
        ig_success = ig_result.returncode == 0
        ig_msg = 'Instagram access working' if ig_success else 'Instagram access failed'
    except Exception as e:
        ig_success = False
        ig_msg = f'Instagram test error: {e}'
    return jsonify({'success': t_success and ig_success, 'message': f'TikTok: {t_msg}; Instagram: {ig_msg}', 'tiktok': t_success, 'instagram': ig_success, 'instagram_cookies_active': bool(get_setting('instagram_active_cookies','')) })

@app.route('/api/test_instagram_highlights/<username>')
def test_instagram_highlights_endpoint(username):
    """Test Instagram highlights access for debugging."""
    success, output = test_instagram_highlights_access(username)
    return jsonify({
        'success': success, 
        'message': 'Highlights access test completed',
        'output': output,
        'url': f'https://www.instagram.com/{username}/highlights/',
        'command': 'gallery-dl --dump-json --no-download <URL>'
    })

@app.route('/api/test_instagram_stories/<username>')
def test_instagram_stories_endpoint(username):
    """Test Instagram stories access for debugging."""
    try:
        # Test stories access
        test_cmd = [
            'gallery-dl', 
            '--dump-json', 
            '--no-download',
            f'https://www.instagram.com/stories/{username}/'
        ]
        
        active = get_setting('instagram_active_cookies','') or ''
        cookie_path = os.path.join('data','cookies','instagram', active) if active else ''
        if active and os.path.exists(cookie_path):
            test_cmd.extend(['--cookies', cookie_path])
        
        result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=30)
        
        output = result.stdout + result.stderr
        success = result.returncode == 0
        
        return jsonify({
            'success': success, 
            'message': 'Stories access test completed',
            'output': output,
            'url': f'https://www.instagram.com/stories/{username}/',
            'command': 'gallery-dl --dump-json --no-download <URL>'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f"Error testing Instagram stories access: {str(e)}",
            'output': str(e),
            'url': f'https://www.instagram.com/stories/{username}/',
            'command': 'gallery-dl --dump-json --no-download <URL>'
        })

@app.route('/avatar/<username>')
def get_avatar(username):
    """Serve user avatar."""
    platform = request.args.get('platform')
    
    # Try with platform prefix if platform provided
    if platform:
        for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']:
            filename = f"{platform}_{username}{ext}"
            path = os.path.join(AVATARS_PATH, filename)
            if os.path.exists(path):
                return send_file(path)

    # Try all prefixes if no platform specific found or platform not provided
    for prefix_plat in ['tiktok', 'instagram', 'coomer']:
        for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']:
            filename = f"{prefix_plat}_{username}{ext}"
            path = os.path.join(AVATARS_PATH, filename)
            if os.path.exists(path):
                return send_file(path)
                
    # Legacy fallback (no prefix)
    for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']:
        filename = f"{username}{ext}"
        path = os.path.join(AVATARS_PATH, filename)
        if os.path.exists(path):
            return send_file(path)
            
    return abort(404)

@app.route('/api/refresh_avatar/<username>', methods=['POST'])
def refresh_user_avatar(username):
    """Refresh avatar for a specific user."""
    try:
        # Get user's platform
        conn = get_db_connection()
        user = conn.execute('SELECT platform FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        
        if not user:
            return jsonify({
                'success': False, 
                'error': 'User not found'
            })
            
        platform = user['platform']
        
        # Remove existing avatar files
        for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']:
            old_path = os.path.join(AVATARS_PATH, f"{platform}_{username}{ext}")
            if os.path.exists(old_path):
                os.remove(old_path)
            # Also remove old format without platform prefix for backwards compatibility
            old_path_legacy = os.path.join(AVATARS_PATH, f"{username}{ext}")
            if os.path.exists(old_path_legacy):
                os.remove(old_path_legacy)
        
        # Download new avatar
        local_avatar = download_avatar_with_gallery_dl(username, platform)
        
        if local_avatar:
            return jsonify({
                'success': True, 
                'message': 'Avatar refreshed successfully',
                'avatar_path': os.path.basename(local_avatar)
            })
        else:
            return jsonify({
                'success': False, 
                'error': 'Failed to download avatar'
            })
            
    except Exception as e:
        return jsonify({
            'success': False, 
            'error': f'Error refreshing avatar: {str(e)}'
        })
def list_ig_cookies():
    base = os.path.join('data','cookies','instagram')
    os.makedirs(base, exist_ok=True)
    files = []
    for fname in os.listdir(base):
        if fname.lower().endswith('.txt'):
            path = os.path.join(base, fname)
            files.append({'name': fname, 'size': os.path.getsize(path), 'mtime': os.path.getmtime(path)})
    return jsonify({'files': files, 'active': get_setting('instagram_active_cookies','')})

@app.route('/api/ig_cookies/upload', methods=['POST'])
def upload_ig_cookies():
    base = os.path.join('data','cookies','instagram')
    os.makedirs(base, exist_ok=True)
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file provided'}), 400
    f = request.files['file']
    name = secure_filename(f.filename)
    if not name.lower().endswith('.txt'):
        name += '.txt'
    path = os.path.join(base, name)
    f.save(path)
    return jsonify({'success': True, 'name': name})

@app.route('/api/ig_cookies/activate', methods=['POST'])
def activate_ig_cookies():
    data = request.get_json(force=True, silent=True) or {}
    name = data.get('name','')
    base = os.path.join('data','cookies','instagram')
    if not name:
        set_setting('instagram_active_cookies','')
        return jsonify({'success': True, 'active': ''})
    path = os.path.join(base, name)
    if not os.path.exists(path):
        return jsonify({'success': False, 'error': 'File not found'}), 404
    set_setting('instagram_active_cookies', name)
    return jsonify({'success': True, 'active': name})

@app.route('/api/ig_cookies/<name>', methods=['DELETE'])
def delete_ig_cookie(name):
    base = os.path.join('data','cookies','instagram')
    path = os.path.join(base, secure_filename(name))
    if os.path.exists(path):
        os.remove(path)
        if get_setting('instagram_active_cookies','') == name:
            set_setting('instagram_active_cookies','')
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'File not found'}), 404

# Instagram Following API endpoints
@app.route('/api/instagram_following/upload_cookie', methods=['POST'])
def upload_instagram_following_cookie():
    """Upload and validate Instagram cookies for following list access."""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'}), 400
        
        file = request.files['file']
        if not file or file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        # Ensure cookies directory exists
        base_dir = os.path.join('data', 'cookies', 'instagram')
        os.makedirs(base_dir, exist_ok=True)
        
        # Generate unique filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"following_{timestamp}.txt"
        
        # Save the file
        file_path = os.path.join(base_dir, filename)
        file.save(file_path)
        
        # First, validate the cookie file format
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            
            # Check if it's a valid Netscape cookie file format
            if not content:
                os.remove(file_path)
                return jsonify({
                    'success': False, 
                    'error': 'Cookie file is empty. Please check your cookie file.'
                })
            
            # Look for Instagram-related cookies
            has_instagram_cookies = False
            lines = content.split('\n')
            
            for line in lines:
                line = line.strip()
                # Skip comments and empty lines
                if line.startswith('#') or not line:
                    continue
                    
                # Check if line contains Instagram domain
                if '.instagram.com' in line or 'instagram.com' in line:
                    has_instagram_cookies = True
                    break
            
            if not has_instagram_cookies:
                os.remove(file_path)
                return jsonify({
                    'success': False, 
                    'error': 'Cookie file does not contain Instagram cookies. Please make sure you downloaded cookies from Instagram.com.'
                })
                
            print(f"Cookie file validation passed: {filename}")
            
        except UnicodeDecodeError:
            os.remove(file_path)
            return jsonify({
                'success': False, 
                'error': 'Cookie file has invalid encoding. Please save as UTF-8 text file.'
            })
        except Exception as e:
            os.remove(file_path)
            print(f"Cookie file validation error: {str(e)}")
            return jsonify({
                'success': False, 
                'error': f'Error reading cookie file: {str(e)}'
            })
        
        # Optional: Test Instagram access (but don't fail if it doesn't work)
        try:
            test_result = subprocess.run([
                'gallery-dl', 
                '--cookies', file_path,
                '--dump-json',
                '--no-download', 
                'https://www.instagram.com/'
            ], capture_output=True, text=True, timeout=15)
            
            if test_result.returncode == 0:
                print(f"Cookie file Instagram access test passed: {filename}")
            else:
                error_output = test_result.stderr or test_result.stdout
                print(f"Cookie file Instagram access test failed (but continuing): {error_output}")
                
        except subprocess.TimeoutExpired:
            print(f"Cookie file Instagram access test timed out (but continuing): {filename}")
        except Exception as e:
            print(f"Cookie file Instagram access test error (but continuing): {str(e)}")
        
        # Set as active cookie for following operations
        set_setting('instagram_following_cookies', filename)
        
        return jsonify({
            'success': True, 
            'filename': filename,
            'message': 'Cookie uploaded and validated successfully'
        })
        
    except Exception as e:
        return jsonify({
            'success': False, 
            'error': f'Error uploading cookie: {str(e)}'
        }), 500

@app.route('/api/instagram_following/fetch', methods=['POST'])
def fetch_instagram_following():
    """Fetch Instagram following list using uploaded cookies."""
    try:
        # Check if we have active cookies
        cookie_filename = get_setting('instagram_following_cookies', '')
        if not cookie_filename:
            return jsonify({
                'success': False, 
                'error': 'No cookies uploaded. Please upload Instagram cookies first.'
            })
        
        cookie_path = os.path.join('data', 'cookies', 'instagram', cookie_filename)
        if not os.path.exists(cookie_path):
            return jsonify({
                'success': False, 
                'error': 'Cookie file not found. Please upload cookies again.'
            })
        
        # First, get current user's username to construct following URL
        # We'll use a generic following endpoint that should work
        following_url = 'https://www.instagram.com/accounts/following/'
        
        print(f"Fetching Instagram following list using cookies: {cookie_filename}")
        
        # Use gallery-dl to fetch following data
        cmd = [
            'gallery-dl',
            '--cookies', cookie_path,
            '--dump-json',
            '--no-download',
            following_url
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            
            if result.returncode != 0:
                error_output = result.stderr or result.stdout
                print(f"Gallery-dl error: {error_output}")
                
                # Try alternative approach - use Instagram's internal API endpoint
                return fetch_following_alternative(cookie_path)
            
            # Parse the JSON output
            output_lines = result.stdout.strip().split('\n')
            following_profiles = []
            
            for line in output_lines:
                if line.strip():
                    try:
                        data = json.loads(line)
                        
                        # Extract profile information
                        if isinstance(data, dict):
                            profile = {
                                'username': data.get('username', ''),
                                'display_name': data.get('full_name', '') or data.get('display_name', ''),
                                'profile_picture': data.get('profile_pic_url', ''),
                                'follower_count': data.get('follower_count', 0),
                                'is_verified': data.get('is_verified', False)
                            }
                            
                            if profile['username']:
                                following_profiles.append(profile)
                    
                    except json.JSONDecodeError:
                        continue
            
            if not following_profiles:
                # Try alternative method
                return fetch_following_alternative(cookie_path)
            
            print(f"Successfully fetched {len(following_profiles)} following profiles")
            
            return jsonify({
                'success': True,
                'following': following_profiles,
                'count': len(following_profiles)
            })
            
        except subprocess.TimeoutExpired:
            return jsonify({
                'success': False,
                'error': 'Request timed out. Instagram may be rate limiting. Please try again later.'
            })
            
    except Exception as e:
        print(f"Error fetching Instagram following: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error fetching following list: {str(e)}'
        }), 500

def fetch_following_alternative(cookie_path):
    """Alternative method to fetch following using Instagram's mobile endpoint."""
    try:
        print("Trying alternative following fetch method...")
        
        # Use Python requests with the cookies to access Instagram's API
        import requests
        from http.cookiejar import MozillaCookieJar
        
        # Load cookies from file
        jar = MozillaCookieJar(cookie_path)
        jar.load(ignore_discard=True, ignore_expires=True)
        
        session = requests.Session()
        session.cookies = jar
        
        # Set headers to mimic browser
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'X-Requested-With': 'XMLHttpRequest'
        })
        
        # First get the main page to get user ID and csrf token
        main_response = session.get('https://www.instagram.com/', timeout=30)
        
        if main_response.status_code != 200:
            return jsonify({
                'success': False,
                'error': 'Failed to access Instagram. Please check your cookies.'
            })
        
        # Extract CSRF token and user ID from the page with multiple patterns
        import re
        
        # Try multiple patterns for CSRF token
        csrf_token = None
        csrf_patterns = [
            r'"csrf_token":"([^"]+)"',
            r'csrf_token"\s*:\s*"([^"]+)"',
            r'csrftoken["\']\s*:\s*["\']([^"\'\']+)["\']',
            r'window\._sharedData\s*=\s*[^;]*csrf_token["\']\s*:\s*["\']([^"\'\']+)["\']',
        ]
        
        for pattern in csrf_patterns:
            match = re.search(pattern, main_response.text, re.IGNORECASE)
            if match:
                csrf_token = match.group(1)
                print(f"Found CSRF token using pattern: {pattern[:30]}...")
                break
        
        # Try multiple patterns for user ID
        user_id = None
        user_id_patterns = [
            r'"viewer":{"id":"([^"]+)"',
            r'"viewerId":"([^"]+)"',
            r'viewer["\']\s*:\s*{[^}]*["\']id["\']\s*:\s*["\']([^"\'\']+)["\']',
            r'window\._sharedData\s*=\s*[^;]*viewer[^}]*id["\']\s*:\s*["\']([^"\'\']+)["\']',
            r'"pk":"([0-9]+)"',
            r'"pk_id":"([0-9]+)"'
        ]
        
        for pattern in user_id_patterns:
            match = re.search(pattern, main_response.text, re.IGNORECASE)
            if match:
                user_id = match.group(1)
                print(f"Found user ID using pattern: {pattern[:30]}...")
                break
        
        print(f"CSRF token found: {bool(csrf_token)}")
        print(f"User ID found: {bool(user_id)}")
        
        if not csrf_token or not user_id:
            # Try alternative method - look for any CSRF token in cookies or meta tags
            if not csrf_token:
                # Try to get CSRF from cookies
                for cookie in session.cookies:
                    if 'csrf' in cookie.name.lower():
                        csrf_token = cookie.value
                        print(f"Found CSRF token in cookies: {cookie.name}")
                        break
                
                # Try to get CSRF from meta tags
                if not csrf_token:
                    csrf_meta_match = re.search(r'<meta[^>]*name=["\']csrf-token["\'][^>]*content=["\']([^"\'\']+)["\']', main_response.text, re.IGNORECASE)
                    if csrf_meta_match:
                        csrf_token = csrf_meta_match.group(1)
                        print("Found CSRF token in meta tag")
            
            if not user_id:
                # Try to extract from different locations
                alt_patterns = [
                    r'"id":"([0-9]+)"[^}]*"username"',
                    r'"user_id":"([0-9]+)"',
                    r'profilePage_([0-9]+)',
                ]
                for pattern in alt_patterns:
                    match = re.search(pattern, main_response.text)
                    if match:
                        user_id = match.group(1)
                        print(f"Found user ID using alternative pattern: {pattern[:30]}...")
                        break
        
        if not csrf_token or not user_id:
            print(f"Final token status - CSRF: {bool(csrf_token)}, User ID: {bool(user_id)}")
            print(f"Response length: {len(main_response.text)} characters")
            # Save a sample of the response for debugging (first 2000 chars)
            sample = main_response.text[:2000]
            print(f"Response sample: {sample}")
            
            return jsonify({
                'success': False,
                'error': f'Could not extract authentication tokens from Instagram. CSRF token: {bool(csrf_token)}, User ID: {bool(user_id)}. This might be due to Instagram changes or rate limiting. Try again later.'
            })
        
        # We already have csrf_token and user_id from the pattern search above
        
        session.headers.update({
            'X-CSRFToken': csrf_token,
            'X-Instagram-AJAX': '1',
            'Referer': 'https://www.instagram.com/'
        })
        
        # Fetch following list using Instagram's GraphQL endpoint
        following_profiles = []
        has_next = True
        end_cursor = ''
        max_pages = 10  # Limit to prevent infinite loops
        page_count = 0
        
        while has_next and page_count < max_pages:
            variables = {
                "id": user_id,
                "include_reel": True,
                "fetch_mutual": False,
                "first": 50
            }
            
            if end_cursor:
                variables["after"] = end_cursor
            
            # Instagram's GraphQL query hash for following list (may change)
            query_hash = "3dec7e2c57367ef3da3d987d89f9dbc8"  # This is a known hash for following queries
            
            params = {
                'query_hash': query_hash,
                'variables': json.dumps(variables)
            }
            
            response = session.get(
                'https://www.instagram.com/graphql/query/',
                params=params,
                timeout=30
            )
            
            if response.status_code != 200:
                print(f"GraphQL request failed with status {response.status_code}")
                break
            
            try:
                data = response.json()
                
                if 'data' in data and 'user' in data['data'] and 'edge_follow' in data['data']['user']:
                    edges = data['data']['user']['edge_follow']['edges']
                    page_info = data['data']['user']['edge_follow']['page_info']
                    
                    for edge in edges:
                        node = edge['node']
                        profile = {
                            'username': node.get('username', ''),
                            'display_name': node.get('full_name', ''),
                            'profile_picture': node.get('profile_pic_url', ''),
                            'follower_count': node.get('edge_followed_by', {}).get('count', 0),
                            'is_verified': node.get('is_verified', False)
                        }
                        
                        if profile['username']:
                            following_profiles.append(profile)
                    
                    has_next = page_info.get('has_next_page', False)
                    end_cursor = page_info.get('end_cursor', '')
                    
                else:
                    print(f"Unexpected API response structure: {data}")
                    break
                    
            except json.JSONDecodeError as e:
                print(f"Failed to parse GraphQL response: {e}")
                break
            
            page_count += 1
            
            # Brief delay to avoid rate limiting
            import time
            time.sleep(1)
        
        if not following_profiles:
            # Try even simpler method - basic following page scraping
            print("Trying basic following page scraping...")
            try:
                following_response = session.get(
                    'https://www.instagram.com/accounts/following/',
                    timeout=30
                )
                
                if following_response.status_code == 200:
                    # Look for user mentions and profile data in the HTML with improved patterns
                    import re
                    
                    # Enhanced pattern to capture profile pictures too
                    profile_patterns = [
                        # Pattern 1: Profile links with images
                        r'href="/([^/"]{1,30})/"[^>]*>.*?<img[^>]+src="([^"]+)"[^>]*alt="([^"]*)',
                        # Pattern 2: JSON data blocks that might contain profile info
                        r'"username":"([^"]{1,30})","full_name":"([^"]*)","profile_pic_url":"([^"]+)"',
                        # Pattern 3: Basic username mentions
                        r'@([a-zA-Z0-9_.]{1,30})\b',
                        # Pattern 4: User profile data in script tags
                        r'"([^"]{1,30})":{[^}]*"profile_pic_url":"([^"]+)"[^}]*"full_name":"([^"]*)"}'
                    ]
                    
                    simple_profiles = []
                    seen_usernames = set()
                    
                    # Try each pattern
                    for pattern in profile_patterns:
                        matches = re.findall(pattern, following_response.text, re.IGNORECASE | re.DOTALL)
                        
                        for match in matches:
                            if len(match) == 3:  # username, display_name/pic, profile_pic/display_name
                                if pattern == profile_patterns[0]:  # href pattern with img
                                    username = match[0]
                                    profile_picture = match[1] if match[1].startswith('http') else ''
                                    display_name = match[2] or username
                                elif pattern == profile_patterns[1]:  # JSON pattern
                                    username = match[0]
                                    display_name = match[1]
                                    profile_picture = match[2]
                                elif pattern == profile_patterns[3]:  # User data pattern
                                    username = match[0]
                                    profile_picture = match[1]
                                    display_name = match[2]
                                else:
                                    continue
                            elif len(match) == 1:  # Simple username pattern
                                username = match[0]
                                display_name = username
                                profile_picture = ''
                            else:
                                continue
                            
                            # Validate and add username
                            if (username and len(username) > 0 and len(username) <= 30 and 
                                username not in seen_usernames and not username.startswith('_')):
                                
                                # Clean display name
                                if not display_name or display_name == username:
                                    display_name = username
                                
                                simple_profiles.append({
                                    'username': username,
                                    'display_name': display_name,
                                    'profile_picture': profile_picture,
                                    'follower_count': 0,
                                    'is_verified': False
                                })
                                seen_usernames.add(username)
                    
                    if simple_profiles:
                        print(f"Basic scraping found {len(simple_profiles)} profiles")
                        return jsonify({
                            'success': True,
                            'following': simple_profiles[:100],  # Limit to 100 for safety
                            'count': len(simple_profiles[:100]),
                            'method': 'basic_scraping'
                        })
                    
            except Exception as scraping_error:
                print(f"Basic scraping failed: {str(scraping_error)}")
            
            return jsonify({
                'success': False,
                'error': 'No following profiles found using any method. This could be due to privacy settings, rate limiting, or expired cookies. Please try: 1) Getting fresh cookies, 2) Waiting a few minutes and trying again, 3) Making sure your Instagram account has a public following list.'
            })
        
        print(f"Successfully fetched {len(following_profiles)} following profiles using alternative method")
        
        return jsonify({
            'success': True,
            'following': following_profiles,
            'count': len(following_profiles)
        })
        
    except requests.RequestException as e:
        return jsonify({
            'success': False,
            'error': f'Network error while fetching following list: {str(e)}'
        })
    except Exception as e:
        print(f"Alternative following fetch error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error with alternative method: {str(e)}'
        })

@app.route('/api/instagram_following/add_selected', methods=['POST'])
def add_selected_instagram_profiles():
    """Add selected Instagram profiles to tracking database."""
    try:
        data = request.get_json()
        if not data or 'usernames' not in data:
            return jsonify({'success': False, 'error': 'No usernames provided'}), 400
        
        usernames = data['usernames']
        if not isinstance(usernames, list) or len(usernames) == 0:
            return jsonify({'success': False, 'error': 'Invalid usernames format'}), 400
        
        added_count = 0
        skipped_count = 0
        errors = []
        
        conn = get_db_connection()
        
        for username in usernames:
            try:
                # Clean username
                clean_username = username.strip().replace('@', '')
                if not clean_username:
                    continue
                
                # Check if user already exists
                existing = conn.execute(
                    'SELECT id FROM users WHERE username = ? AND platform = ?',
                    (clean_username, 'instagram')
                ).fetchone()
                
                if existing:
                    skipped_count += 1
                    continue
                
                # Add new user with default values
                conn.execute('''
                    INSERT INTO users (
                        username, platform, display_name, is_tracking, created_at
                    ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (clean_username, 'instagram', clean_username, 1))
                
                added_count += 1
                print(f"Added Instagram user: {clean_username}")
                
            except Exception as e:
                errors.append(f"Error adding {username}: {str(e)}")
                continue
        
        conn.commit()
        conn.close()
        
        # Start background sync for newly added users to get their profile info
        if added_count > 0:
            def background_sync():
                for username in usernames:
                    clean_username = username.strip().replace('@', '')
                    if clean_username:
                        try:
                            success, message = update_user_stats(clean_username, 'instagram')
                            if success:
                                print(f"Updated stats for {clean_username}: {message}")
                            else:
                                print(f"Failed to update stats for {clean_username}: {message}")
                        except Exception as e:
                            print(f"Error updating stats for {clean_username}: {e}")
                        
                        # Brief delay between updates
                        import time
                        time.sleep(2)
            
            # Start background thread
            threading.Thread(target=background_sync, daemon=True).start()
        
        result = {
            'success': True,
            'added': added_count,
            'skipped': skipped_count,
            'message': f'Successfully processed {len(usernames)} profiles'
        }
        
        if errors:
            result['errors'] = errors
        
        return jsonify(result)
        
    except Exception as e:
        print(f"Error adding selected profiles: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error adding profiles: {str(e)}'
        }), 500

@app.route('/api/instagram_following/debug_cookie', methods=['POST'])
def debug_instagram_cookie():
    """Debug endpoint to analyze cookie file content."""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'}), 400
        
        file = request.files['file']
        if not file or file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        # Read file content without saving
        content = file.read().decode('utf-8', errors='replace')
        lines = content.split('\n')
        
        analysis = {
            'total_lines': len(lines),
            'non_empty_lines': 0,
            'comment_lines': 0,
            'instagram_lines': 0,
            'cookie_lines': 0,
            'sample_lines': [],
            'instagram_domains': set(),
            'cookie_names': set()
        }
        
        for i, line in enumerate(lines[:100]):  # Only analyze first 100 lines
            line = line.strip()
            
            if not line:
                continue
                
            analysis['non_empty_lines'] += 1
            
            if line.startswith('#'):
                analysis['comment_lines'] += 1
                if i < 10:  # Sample first 10 lines
                    analysis['sample_lines'].append(f"Line {i+1} (comment): {line[:100]}")
                continue
            
            # Try to parse as cookie line
            parts = line.split('\t')
            if len(parts) >= 6:  # Netscape format has at least 6 columns
                analysis['cookie_lines'] += 1
                domain = parts[0]
                cookie_name = parts[5] if len(parts) > 5 else 'unknown'
                
                if 'instagram' in domain.lower():
                    analysis['instagram_lines'] += 1
                    analysis['instagram_domains'].add(domain)
                    analysis['cookie_names'].add(cookie_name)
                    
                    if len(analysis['sample_lines']) < 5:
                        analysis['sample_lines'].append(f"Line {i+1} (IG cookie): {domain} - {cookie_name}")
                        
            elif i < 10:  # Sample unusual lines
                analysis['sample_lines'].append(f"Line {i+1} (unusual): {line[:100]}")
        
        # Convert sets to lists for JSON serialization
        analysis['instagram_domains'] = list(analysis['instagram_domains'])
        analysis['cookie_names'] = list(analysis['cookie_names'])
        
        return jsonify({
            'success': True,
            'filename': file.filename,
            'file_size': len(content),
            'analysis': analysis
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Error analyzing cookie file: {str(e)}'
        }), 500

@app.route('/api/instagram_following/test_access', methods=['POST'])
def test_instagram_access():
    """Debug endpoint to test Instagram access with uploaded cookies."""
    try:
        # Check if we have active cookies
        cookie_filename = get_setting('instagram_following_cookies', '')
        if not cookie_filename:
            return jsonify({
                'success': False, 
                'error': 'No cookies uploaded. Please upload Instagram cookies first.'
            })
        
        cookie_path = os.path.join('data', 'cookies', 'instagram', cookie_filename)
        if not os.path.exists(cookie_path):
            return jsonify({
                'success': False, 
                'error': 'Cookie file not found. Please upload cookies again.'
            })
        
        print(f"Testing Instagram access with cookies: {cookie_filename}")
        
        # Test with requests library
        import requests
        from http.cookiejar import MozillaCookieJar
        
        jar = MozillaCookieJar(cookie_path)
        jar.load(ignore_discard=True, ignore_expires=True)
        
        session = requests.Session()
        session.cookies = jar
        
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
        
        response = session.get('https://www.instagram.com/', timeout=30)
        
        # Analyze the response
        analysis = {
            'status_code': response.status_code,
            'response_size': len(response.text),
            'cookies_loaded': len(list(jar)),
            'response_cookies': len(response.cookies),
            'headers': dict(response.headers),
            'logged_in_indicators': {},
            'auth_tokens': {}
        }
        
        # Check for login indicators
        text_lower = response.text.lower()
        analysis['logged_in_indicators'] = {
            'has_login_form': 'type="password"' in text_lower,
            'has_logout_link': 'logout' in text_lower,
            'has_feed_content': 'feed' in text_lower or 'timeline' in text_lower,
            'has_profile_link': 'profile' in text_lower,
            'has_viewer_data': 'viewer' in response.text,
            'has_shared_data': '_sharedData' in response.text
        }
        
        # Try to extract tokens
        import re
        csrf_match = re.search(r'"csrf_token":"([^"]+)"', response.text)
        user_id_match = re.search(r'"viewer":{"id":"([^"]+)"', response.text)
        
        analysis['auth_tokens'] = {
            'csrf_token_found': bool(csrf_match),
            'csrf_token_preview': csrf_match.group(1)[:10] + '...' if csrf_match else None,
            'user_id_found': bool(user_id_match),
            'user_id': user_id_match.group(1) if user_id_match else None
        }
        
        # Get sample of response
        analysis['response_sample'] = response.text[:1000] + '...' if len(response.text) > 1000 else response.text
        
        success = response.status_code == 200 and (analysis['logged_in_indicators']['has_viewer_data'] or not analysis['logged_in_indicators']['has_login_form'])
        
        return jsonify({
            'success': success,
            'message': 'Access test completed',
            'analysis': analysis
        })
        
    except Exception as e:
        print(f"Error testing Instagram access: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error testing Instagram access: {str(e)}'
        }), 500

@app.route('/api/instagram_following/fetch_profile_pics', methods=['POST'])
def fetch_profile_pictures():
    """Fetch profile pictures for a list of usernames."""
    try:
        data = request.get_json()
        if not data or 'usernames' not in data:
            return jsonify({'success': False, 'error': 'No usernames provided'}), 400
        
        usernames = data['usernames']
        if not isinstance(usernames, list) or len(usernames) == 0:
            return jsonify({'success': False, 'error': 'Invalid usernames format'}), 400
        
        # Check if we have active cookies
        cookie_filename = get_setting('instagram_following_cookies', '')
        if not cookie_filename:
            return jsonify({
                'success': False, 
                'error': 'No cookies uploaded. Please upload Instagram cookies first.'
            })
        
        cookie_path = os.path.join('data', 'cookies', 'instagram', cookie_filename)
        if not os.path.exists(cookie_path):
            return jsonify({
                'success': False, 
                'error': 'Cookie file not found. Please upload cookies again.'
            })
        
        print(f"Fetching profile pictures for {len(usernames)} users")
        
        import requests
        from http.cookiejar import MozillaCookieJar
        
        jar = MozillaCookieJar(cookie_path)
        jar.load(ignore_discard=True, ignore_expires=True)
        
        session = requests.Session()
        session.cookies = jar
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        
        profile_pics = {}
        successful_fetches = 0
        
        # Limit to first 20 profiles to avoid overwhelming Instagram
        limited_usernames = usernames[:20]
        
        for username in limited_usernames:
            try:
                profile_url = f"https://www.instagram.com/{username}/"
                response = session.get(profile_url, timeout=15)
                
                if response.status_code == 200:
                    # Look for profile picture in the HTML
                    import re
                    pic_patterns = [
                        r'"profile_pic_url":"([^"]+)"',
                        r'"profile_pic_url_hd":"([^"]+)"',
                        r'property="og:image"\s+content="([^"]+)"',
                        r'<img[^>]+src="([^"]+)"[^>]*alt="[^"]*profile[^"]*picture',
                    ]
                    
                    for pattern in pic_patterns:
                        match = re.search(pattern, response.text, re.IGNORECASE)
                        if match:
                            pic_url = match.group(1)
                            # Clean up URL (remove escapes)
                            pic_url = pic_url.replace('\\u0026', '&').replace('\\/', '/')
                            if pic_url.startswith('http'):
                                profile_pics[username] = pic_url
                                successful_fetches += 1
                                print(f"Found profile pic for {username}")
                                break
                
                # Small delay between requests
                import time
                time.sleep(0.5)
                
            except Exception as e:
                print(f"Error fetching profile pic for {username}: {str(e)}")
                continue
        
        return jsonify({
            'success': True,
            'profile_pictures': profile_pics,
            'fetched_count': successful_fetches,
            'total_requested': len(limited_usernames)
        })
        
    except Exception as e:
        print(f"Error fetching profile pictures: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Error fetching profile pictures: {str(e)}'
        }), 500

# Scheduler

@app.route('/api/scheduler/status')
def get_scheduler_status():
    """Get current scheduler status and recent logs."""
    enabled = get_bool_setting('schedule_enabled', False)
    last_run_iso = get_setting('schedule_last_run', '')
    
    last_run = None
    if last_run_iso:
        try:
            last_run = datetime.fromisoformat(last_run_iso).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            last_run = None
    
    # Calculate next run time
    next_run = None
    if enabled:
        try:
            now = datetime.now()
            freq = get_setting('schedule_frequency', 'daily')
            time_str = get_setting('schedule_time', '03:00') or '03:00'
            hour, minute = [int(x) for x in time_str.split(':')[:2]]
            
            if freq == 'daily':
                next_run_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if now >= next_run_time:
                    next_run_time += timedelta(days=1)
                next_run = next_run_time.strftime('%Y-%m-%d %H:%M:%S')
            else:  # weekly
                target_day = int(get_setting('schedule_day', '0') or 0)
                days_ahead = target_day - now.weekday()
                if days_ahead <= 0:  # Target day already passed this week
                    days_ahead += 7
                next_run_time = (now + timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
                next_run = next_run_time.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            next_run = "Error calculating next run"
    
    return jsonify({
        'enabled': enabled,
        'running': scheduler_started,
        'frequency': get_setting('schedule_frequency', 'daily'),
        'time': get_setting('schedule_time', '03:00'),
        'day': int(get_setting('schedule_day', '0') or 0),
        'last_run': last_run,
        'next_run': next_run,
        'recent_logs': scheduler_logs[-20:] if scheduler_logs else []
    })

@app.route('/api/scheduler/logs')
def get_scheduler_logs():
    """Get full scheduler logs."""
    return jsonify({
        'success': True,
        'logs': scheduler_logs
    })

def start_scheduler_thread():
    global scheduler_started, scheduler_logs
    if scheduler_started:
        return
    scheduler_started = True
    
    def log_scheduler(message):
        """Add timestamped log entry for scheduler"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_entry = f"[{timestamp}] {message}"
        scheduler_logs.append(log_entry)
        print(f"SCHEDULER: {log_entry}")
        # Keep only last 200 logs
        if len(scheduler_logs) > 200:
            scheduler_logs.pop(0)

    def scheduler_loop():
        log_scheduler("✅ Scheduler thread started successfully")
        
        while True:
            try:
                enabled = get_bool_setting('schedule_enabled', False)
                
                if not enabled:
                    time.sleep(60)
                    continue
                
                if sync_status.get('running', False):
                    log_scheduler("⏭️ Skipping check - sync already running")
                    time.sleep(60)
                    continue
                
                now = datetime.now()
                freq = get_setting('schedule_frequency', 'daily')
                time_str = get_setting('schedule_time', '03:00') or '03:00'
                
                try:
                    hour, minute = [int(x) for x in time_str.split(':')[:2]]
                except Exception as e:
                    log_scheduler(f"⚠️ Invalid time format '{time_str}', using default 03:00")
                    hour, minute = 3, 0
                
                due_today = now.hour > hour or (now.hour == hour and now.minute >= minute)

                last_run_iso = get_setting('schedule_last_run', '')
                last_run_ok = None
                if last_run_iso:
                    try:
                        last_run_ok = datetime.fromisoformat(last_run_iso)
                    except Exception:
                        last_run_ok = None

                should_run = False
                reason = ""
                
                if freq == 'daily':
                    # Not already run today and time passed
                    if due_today:
                        if not last_run_ok:
                            should_run = True
                            reason = f"First run at {time_str}"
                        elif last_run_ok.date() != now.date():
                            should_run = True
                            reason = f"Daily sync at {time_str} (last: {last_run_ok.strftime('%Y-%m-%d')})"
                        else:
                            reason = f"Already ran today at {last_run_ok.strftime('%H:%M')}"
                    else:
                        reason = f"Waiting for {time_str} (now: {now.strftime('%H:%M')})"
                else:  # weekly
                    target_day = int(get_setting('schedule_day', '0') or 0)
                    day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
                    is_day = now.weekday() == target_day
                    
                    if is_day and due_today:
                        # Only once per week (compare ISO week number)
                        if not last_run_ok or (last_run_ok.isocalendar()[:2] != now.isocalendar()[:2]):
                            should_run = True
                            reason = f"Weekly sync on {day_names[target_day]} at {time_str}"
                        else:
                            reason = f"Already ran this week on {last_run_ok.strftime('%A %H:%M')}"
                    else:
                        if not is_day:
                            reason = f"Waiting for {day_names[target_day]} (today: {day_names[now.weekday()]})"
                        else:
                            reason = f"Waiting for {time_str} (now: {now.strftime('%H:%M')})"
                
                if should_run:
                    log_scheduler(f"🚀 Starting scheduled sync - {reason}")
                    # Start sync in background
                    threading.Thread(target=run_sync_all_process, daemon=True).start()
                    set_setting('schedule_last_run', now.isoformat())
                    log_scheduler(f"✅ Sync initiated successfully")
                time.sleep(60)
            except Exception as e:
                log_scheduler(f"❌ Error in scheduler: {str(e)}")
                # Never crash scheduler
                time.sleep(60)

    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()


def get_all_google_drive_files_from_folder(folder_url, max_files=500):
    """Extract all file IDs from a Google Drive folder, handling the 50-file limit.
    Uses multiple techniques to get complete file listings.
    Returns list of (file_id, filename) tuples.
    """
    try:
        import tempfile
        import re
        import time
        
        all_file_info = []
        
        # Method 1: Use gdown with remaining-ok to get initial batch
        with tempfile.TemporaryDirectory() as temp_dir:
            print("Getting Google Drive folder contents (this may take a moment for large folders)...")
            
            # First, try to get the complete listing with remaining-ok
            list_cmd = [
                'gdown',
                '--folder',
                '--remaining-ok',  # Don't stop at 50 files
                '--quiet',  # Reduce noise
                '--output', temp_dir,
                folder_url
            ]
            
            # Run with longer timeout for large folders
            result = subprocess.run(
                list_cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout for large folders
            )
            
            file_info = []
            if result.stdout:
                # Parse the output to extract file IDs and names
                lines = result.stdout.split('\n')
                for line in lines:
                    if 'Processing file' in line:
                        # Extract file ID and filename from lines like:
                        # "Processing file 1P6SOQjehpb3NFMCKI9gONP8gIt06bm-F IMG_0671.JPG"
                        parts = line.split(' ')
                        if len(parts) >= 4:
                            file_id = parts[2]  # The file ID
                            filename = parts[-1]  # The filename
                            file_info.append((file_id, filename))
            
            print(f"Found {len(file_info)} files in Google Drive folder")
            
            # If we got exactly 50 files, there might be more (Google Drive limit)
            if len(file_info) == 50:
                print("Warning: Found exactly 50 files - there may be more files in this folder.")
                print("Google Drive has a 50-file download limit per request.")
                
                # Try to get more files by attempting individual API calls
                # This is a workaround for the 50-file limit
                try:
                    # Method 2: Try alternative approach using folder ID extraction
                    folder_id = extract_folder_id_from_url(folder_url)
                    if folder_id:
                        additional_files = get_additional_drive_files(folder_id, temp_dir)
                        if additional_files:
                            print(f"Found {len(additional_files)} additional files using alternative method")
                            file_info.extend(additional_files)
                except Exception as e:
                    print(f"Could not get additional files: {e}")
                    print("Will proceed with the first 50 files found.")
            
            return file_info[:max_files]  # Limit to prevent excessive downloads
            
    except Exception as e:
        print(f"Error extracting Google Drive file IDs: {e}")
        return []

def extract_folder_id_from_url(folder_url):
    """Extract the folder ID from a Google Drive folder URL."""
    import re
    
    # Match various Google Drive folder URL formats
    patterns = [
        r'/folders/([a-zA-Z0-9-_]+)',
        r'id=([a-zA-Z0-9-_]+)',
        r'/drive/folders/([a-zA-Z0-9-_]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, folder_url)
        if match:
            return match.group(1)
    
    return None

def get_additional_drive_files(folder_id, temp_dir):
    """Try to get additional files beyond the 50-file limit.
    This uses alternative gdown approaches to get more files.
    """
    additional_files = []
    
    try:
        # Try using the folder ID directly with different parameters
        direct_url = f"https://drive.google.com/drive/folders/{folder_id}"
        
        # Method: Use gdown with different flags
        alt_cmd = [
            'gdown',
            '--folder',
            '--remaining-ok',
            '--continue',
            '--quiet',
            '--output', temp_dir,
            direct_url
        ]
        
        result = subprocess.run(
            alt_cmd,
            capture_output=True,
            text=True,
            timeout=180
        )
        
        if result.stdout:
            lines = result.stdout.split('\n')
            for line in lines:
                if 'Processing file' in line:
                    parts = line.split(' ')
                    if len(parts) >= 4:
                        file_id = parts[2]
                        filename = parts[-1]
                        additional_files.append((file_id, filename))
        
    except Exception as e:
        print(f"Alternative method failed: {e}")
    
    return additional_files

def download_google_drive_files_in_batches(file_info_list, output_dir, progress_callback=None, batch_size=10):
    """Download Google Drive files in batches to handle large folders efficiently.
    Returns (success_count: int, total_attempted: int)
    """
    success_count = 0
    total_files = len(file_info_list)
    
    print(f"Downloading {total_files} files in batches of {batch_size}...")
    
    # Split files into batches
    for batch_start in range(0, total_files, batch_size):
        batch_end = min(batch_start + batch_size, total_files)
        batch = file_info_list[batch_start:batch_end]
        batch_num = (batch_start // batch_size) + 1
        total_batches = (total_files + batch_size - 1) // batch_size
        
        print(f"Processing batch {batch_num}/{total_batches} ({len(batch)} files)")
        if progress_callback:
            progress_callback(success_count, f"Batch {batch_num}/{total_batches}: Processing {len(batch)} files...")
        
        # Download files in current batch
        for i, (file_id, filename) in enumerate(batch):
            try:
                global_index = batch_start + i + 1
                if progress_callback:
                    progress_callback(success_count, f"Downloading {filename} ({global_index}/{total_files})")
                
                # Create individual file URL
                file_url = f"https://drive.google.com/uc?id={file_id}"
                
                # Download individual file with optimized settings
                file_cmd = [
                    'gdown',
                    '--fuzzy',
                    '--continue',
                    '--quiet',  # Reduce output noise for batch downloads
                    '--output', os.path.join(output_dir, filename),
                    file_url
                ]
                
                file_result = subprocess.run(
                    file_cmd,
                    capture_output=True,
                    text=True,
                    timeout=180  # 3 minute timeout per file for batch mode
                )
                
                if file_result.returncode == 0 and os.path.exists(os.path.join(output_dir, filename)):
                    success_count += 1
                    print(f"✓ Downloaded: {filename}")
                else:
                    print(f"✗ Failed: {filename}")
                    # Try once more with different flags for failed files
                    retry_cmd = [
                        'gdown',
                        '--no-cookies',
                        '--output', os.path.join(output_dir, filename),
                        file_url
                    ]
                    
                    retry_result = subprocess.run(
                        retry_cmd,
                        capture_output=True,
                        text=True,
                        timeout=120
                    )
                    
                    if retry_result.returncode == 0 and os.path.exists(os.path.join(output_dir, filename)):
                        success_count += 1
                        print(f"✓ Downloaded on retry: {filename}")
                    else:
                        print(f"✗ Failed on retry: {filename}")
                        
            except Exception as e:
                print(f"Error downloading {filename}: {e}")
            
            # Brief pause between downloads to avoid rate limiting
            import time
            time.sleep(0.5)  # Shorter delay for batch mode
        
        # Longer pause between batches
        if batch_end < total_files:  # Not the last batch
            print(f"Batch {batch_num} completed. Pausing before next batch...")
            import time
            time.sleep(2)  # 2 second pause between batches
    
    print(f"Batch download completed: {success_count}/{total_files} files successful")
    return success_count, total_files

def download_google_drive_files_individually(file_info_list, output_dir, progress_callback=None):
    """Download Google Drive files individually using their file IDs.
    Uses batched approach for large numbers of files.
    Returns (success_count: int, total_attempted: int)
    """
    # For large numbers of files, use batch download
    if len(file_info_list) > 20:
        return download_google_drive_files_in_batches(file_info_list, output_dir, progress_callback, batch_size=15)
    
    # For smaller numbers, download one by one with more detailed progress
    success_count = 0
    total_files = len(file_info_list)
    
    for i, (file_id, filename) in enumerate(file_info_list):
        try:
            if progress_callback:
                progress_callback(success_count, f"Downloading {filename} ({i+1}/{total_files})")
            
            # Create individual file URL
            file_url = f"https://drive.google.com/uc?id={file_id}"
            
            # Download individual file
            file_cmd = [
                'gdown',
                '--fuzzy',
                '--continue',
                '--output', os.path.join(output_dir, filename),
                file_url
            ]
            
            file_result = subprocess.run(
                file_cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout per file
            )
            
            if file_result.returncode == 0 and os.path.exists(os.path.join(output_dir, filename)):
                success_count += 1
                print(f"Downloaded: {filename}")
            else:
                print(f"Failed to download: {filename}")
                
        except Exception as e:
            print(f"Error downloading {filename}: {e}")
        
        # Brief pause between downloads to avoid rate limiting
        import time
        time.sleep(1)
    
    return success_count, total_files

def perform_external_download(url, destination_folder=None, progress_callback=None):
    """Perform external download using appropriate tool (gdown for Google Drive, gallery-dl for others).
    Returns (success: bool, output: str, file_count: int, service_name: str)
    """
    try:
        # Detect service type from URL
        service_name = 'unknown'
        if 'drive.google.com' in url or 'docs.google.com' in url:
            service_name = 'googledrive'
        elif 'gofile.io' in url:
            service_name = 'gofile'
        elif 'bunkr.' in url or 'bunkrr.' in url:
            service_name = 'bunkr'
        elif 'imgur.com' in url:
            service_name = 'imgur'
        elif 'catbox.moe' in url:
            service_name = 'catbox'
        elif 'redgifs.com' in url:
            service_name = 'redgifs'
        
        # Create output directory
        if destination_folder:
            output_dir = os.path.join(DOWNLOADS_PATH, 'external', service_name, destination_folder)
        else:
            # Use a timestamp-based folder for organization
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_dir = os.path.join(DOWNLOADS_PATH, 'external', service_name, timestamp)
        
        os.makedirs(output_dir, exist_ok=True)
        
        print(f"Starting external download: {service_name} -> {output_dir}")
        
        # Use appropriate downloader based on service
        if service_name == 'googledrive':
            # Use gdown for Google Drive with progress tracking
            is_folder = ('/folders/' in url) or ('drive.google.com/drive/folders' in url)
            
            # Try multiple download strategies for better success rate
            success_achieved = False
            attempt = 1
            max_attempts = 3
            
            while not success_achieved and attempt <= max_attempts:
                print(f"Google Drive download attempt {attempt}/{max_attempts}")
                if progress_callback:
                    progress_callback(0, f"Attempt {attempt}/{max_attempts}: Starting Google Drive download...")
                
                # Strategy 1: Standard download with optimized flags
                if attempt == 1:
                    cmd = [
                        'gdown',
                        '--fuzzy',
                        '--continue',  # resume interrupted downloads
                    ]
                    
                    if is_folder:
                        cmd.extend(['--folder', '--remaining-ok'])  # allow folders with >50 files
                        cmd.extend(['--output', output_dir])  # folder output
                    else:
                        cmd.extend(['--output', os.path.join(output_dir, '')])
                    
                    cmd.append(url)
                
                # Strategy 2: Use cookies for better authentication
                elif attempt == 2:
                    cmd = [
                        'gdown',
                        '--fuzzy',
                        '--continue',
                    ]
                    
                    if is_folder:
                        cmd.extend(['--folder', '--remaining-ok'])
                        cmd.extend(['--output', output_dir])
                    else:
                        cmd.extend(['--output', os.path.join(output_dir, '')])
                    
                    cmd.append(url)
                
                # Strategy 3: Slower but more reliable method
                elif attempt == 3:
                    cmd = [
                        'gdown',
                        '--fuzzy',
                        '--continue',
                        '--quiet',  # reduce output noise
                    ]
                    
                    if is_folder:
                        cmd.extend(['--folder', '--remaining-ok'])
                        cmd.extend(['--output', output_dir])
                    else:
                        cmd.extend(['--output', os.path.join(output_dir, '')])
                    
                    cmd.append(url)
            
                # Run gdown with real-time output capture for progress tracking
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                    cwd=output_dir
                )
                
                output_lines = []
                files_processed = 0
                
                # Track permission errors and other issues
                permission_errors = []
                processing_files = []
                download_errors = []
                
                # Real-time output processing for progress tracking
                for line in iter(process.stdout.readline, ''):
                    if not line:
                        break
                    line = line.strip()
                    output_lines.append(line)
                    
                    # Track permission errors
                    if 'Cannot retrieve the public link' in line or 'You may need to change the permission' in line:
                        permission_errors.append(line)
                    elif 'Processing file' in line and ('.JPG' in line or '.jpg' in line or '.png' in line or '.mp4' in line):
                        # Extract filename from "Processing file 1P6SOQjehpb3NFMCKI9gONP8gIt06bm-F IMG_0671.JPG"
                        parts = line.split(' ')
                        if len(parts) >= 4:
                            filename = parts[-1]  # Get the last part (filename)
                            processing_files.append(filename)
                            print(f"Processing file: {filename}")
                            if progress_callback:
                                progress_callback(len(processing_files), f"Processing: {filename}")
                    elif 'Failed to retrieve file url' in line or 'Gdown can\'t' in line:
                        download_errors.append(line)
                    
                    # Track progress indicators from gdown
                    if 'Downloading' in line or 'From:' in line:
                        files_processed += 1
                        print(f"Google Drive progress: {line}")
                        if progress_callback:
                            progress_callback(files_processed, f"Downloading file {files_processed}")
                    elif '%' in line and ('|' in line or 'B/s' in line):
                        # Progress bar line
                        print(f"Google Drive progress: {line}")
                        if progress_callback:
                            # Extract current file info from progress line if possible
                            current_info = line[:50] + '...' if len(line) > 50 else line
                            progress_callback(files_processed, current_info)
                    elif 'Done' in line or 'Download completed' in line:
                        files_processed += 1
                        print(f"Google Drive: {line}")
                        if progress_callback:
                            progress_callback(files_processed, f"Completed file {files_processed}")
                
                process.stdout.close()
                return_code = process.wait()
                
                # Check if this attempt was successful
                files_downloaded = 0
                if os.path.exists(output_dir):
                    for root, dirs, files in os.walk(output_dir):
                        files_downloaded += len([f for f in files if f.lower().endswith(('.mp4', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp3', '.wav', '.pdf', '.txt', '.doc', '.docx', '.zip', '.rar', '.pptx', '.xlsx'))])
                
                # Consider successful if we downloaded some files or had no errors
                if files_downloaded > 0 or (return_code == 0 and not permission_errors and not download_errors):
                    success_achieved = True
                    print(f"Google Drive download successful on attempt {attempt}: {files_downloaded} files")
                    break
                else:
                    print(f"Google Drive attempt {attempt} failed. Files: {files_downloaded}, RC: {return_code}")
                    if attempt < max_attempts:
                        print(f"Will retry with different strategy...")
                        if progress_callback:
                            progress_callback(0, f"Retrying download (attempt {attempt+1}/{max_attempts})...")
                        attempt += 1
                        import time
                        time.sleep(2)  # Brief pause before retry
                    else:
                        # Final attempt failed
                        break
            
            # Create result object to match subprocess.run format
            class MockResult:
                def __init__(self, returncode, stdout, stderr=''):
                    self.returncode = returncode
                    self.stdout = stdout
                    self.stderr = stderr
            
            # Final count of downloaded files
            final_file_count = 0
            if os.path.exists(output_dir):
                for root, dirs, files in os.walk(output_dir):
                    final_file_count += len([f for f in files if f.lower().endswith(('.mp4', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp3', '.wav', '.pdf', '.txt', '.doc', '.docx', '.zip', '.rar', '.pptx', '.xlsx'))])
            
            # Check for permission errors and provide helpful feedback
            output_text = '\n'.join(output_lines)
            
            if success_achieved and final_file_count > 0:
                # Success case
                success_message = f"Successfully downloaded {final_file_count} files from Google Drive after {attempt} attempt(s)"
                result = MockResult(0, success_message)
                print(f"Google Drive download completed: {final_file_count} files")
            elif permission_errors and final_file_count == 0:
                # Permission error case
                error_message = (
                    f"Google Drive Permission Error:\n\n"
                    f"The folder/files you're trying to download are not publicly accessible. "
                    f"To fix this:\n\n"
                    f"1. Open the Google Drive folder in your browser\n"
                    f"2. Right-click the folder → Share → Change to 'Anyone with the link'\n"
                    f"3. Set permission to 'Viewer' or 'Editor'\n"
                    f"4. Copy the share link and try downloading again\n\n"
                    f"Files found but couldn't download: {len(processing_files)} files\n"
                    f"{'Sample files: ' + ', '.join(processing_files[:5]) + ('...' if len(processing_files) > 5 else '') if processing_files else ''}"
                )
                result = MockResult(1, output_text, error_message)
            elif download_errors and final_file_count == 0:
                # Try individual file downloads as a fallback for Google Drive
                print("Attempting Google Drive download with individual file method...")
                if progress_callback:
                    progress_callback(0, "Trying individual file download method...")
                
                try:
                    # First, try to get a complete file list using our improved method
                    print("Attempting to get complete file list from Google Drive folder...")
                    if progress_callback:
                        progress_callback(0, "Getting complete file list (may take time for large folders)...")
                    
                    file_info = get_all_google_drive_files_from_folder(url)
                    
                    # If that didn't work, fall back to extracting from the gdown output we already have
                    if not file_info:
                        print("Fallback: Extracting file IDs from gdown output...")
                        for line in output_lines:
                            if 'Processing file' in line and ('.JPG' in line or '.jpg' in line or '.png' in line or '.mp4' in line):
                                parts = line.split(' ')
                                if len(parts) >= 4:
                                    file_id = parts[2]
                                    filename = parts[-1]
                                    file_info.append((file_id, filename))
                    
                    if file_info:
                        total_found = len(file_info)
                        print(f"Found {total_found} files to download individually")
                        
                        # Inform user about large folder handling
                        if total_found >= 50:
                            print(f"Large folder detected ({total_found} files). Using optimized batch download method.")
                            if progress_callback:
                                progress_callback(0, f"Large folder: {total_found} files found. Starting batch downloads...")
                        
                        success_count, total_attempted = download_google_drive_files_individually(
                            file_info, output_dir, progress_callback
                        )
                        
                        if success_count > 0:
                            success_message = f"Successfully downloaded {success_count}/{total_attempted} files from Google Drive using individual file downloads"
                            result = MockResult(0, success_message)
                            print(f"Individual file download succeeded: {success_count}/{total_attempted} files")
                        else:
                            # Individual downloads failed too, try gallery-dl
                            print("Individual downloads failed, trying gallery-dl...")
                            if progress_callback:
                                progress_callback(0, "Trying gallery-dl as final fallback...")
                                
                            gallery_cmd = [
                                'gallery-dl',
                                '--dest', output_dir,
                                '--write-metadata',
                                '--write-info-json',
                                url
                            ]
                            
                            gallery_result = subprocess.run(
                                gallery_cmd,
                                capture_output=True,
                                text=True,
                                timeout=DOWNLOAD_TIMEOUT
                            )
                            
                            # Check if gallery-dl worked
                            gallery_file_count = 0
                            if os.path.exists(output_dir):
                                for root, dirs, files in os.walk(output_dir):
                                    gallery_file_count += len([f for f in files if f.lower().endswith(('.mp4', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp3', '.wav', '.pdf', '.txt', '.doc', '.docx', '.zip', '.rar', '.pptx', '.xlsx'))])
                            
                            if gallery_file_count > 0:
                                success_message = f"Successfully downloaded {gallery_file_count} files from Google Drive using gallery-dl fallback"
                                result = MockResult(0, success_message)
                                print(f"Gallery-dl fallback succeeded: {gallery_file_count} files")
                            else:
                                # All methods failed
                                error_message = (
                                    f"Google Drive Download Error:\n\n"
                                    f"Unable to download files after trying all available methods:\n"
                                    f"1. Folder download with gdown (failed)\n"
                                    f"2. Individual file downloads (failed: {success_count}/{total_attempted})\n"
                                    f"3. Gallery-dl fallback (failed)\n\n"
                                    f"This could be due to:\n"
                                    f"• Google Drive rate limiting (try again later)\n"
                                    f"• Large folder size causing timeouts\n"
                                    f"• Network connectivity issues\n"
                                    f"• Files requiring special permissions\n\n"
                                    f"Files found: {len(file_info)} files\n"
                                    f"Try: Breaking large folders into smaller ones or using direct file links."
                                )
                                result = MockResult(1, output_text, error_message)
                    else:
                        # No file info found, just try gallery-dl
                        print("No file info found, trying gallery-dl directly...")
                        gallery_cmd = [
                            'gallery-dl',
                            '--dest', output_dir,
                            '--write-metadata',
                            '--write-info-json',
                            url
                        ]
                        
                        gallery_result = subprocess.run(
                            gallery_cmd,
                            capture_output=True,
                            text=True,
                            timeout=DOWNLOAD_TIMEOUT
                        )
                        
                        # Check if gallery-dl worked
                        gallery_file_count = 0
                        if os.path.exists(output_dir):
                            for root, dirs, files in os.walk(output_dir):
                                gallery_file_count += len([f for f in files if f.lower().endswith(('.mp4', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp3', '.wav', '.pdf', '.txt', '.doc', '.docx', '.zip', '.rar', '.pptx', '.xlsx'))])
                        
                        if gallery_file_count > 0:
                            success_message = f"Successfully downloaded {gallery_file_count} files from Google Drive using gallery-dl"
                            result = MockResult(0, success_message)
                            print(f"Gallery-dl succeeded: {gallery_file_count} files")
                        else:
                            error_message = (
                                f"Google Drive Download Error:\n\n"
                                f"Unable to download files after trying multiple methods.\n\n"
                                f"Files found but couldn't download: {len(processing_files)} files"
                            )
                            result = MockResult(1, output_text, error_message)
                        
                except Exception as fallback_error:
                    error_message = (
                        f"Google Drive Download Error:\n\n"
                        f"Unable to download files after {max_attempts} attempts with gdown, and all fallback methods failed.\n\n"
                        f"Error: {str(fallback_error)}\n\n"
                        f"Files found: {len(processing_files)} files\n"
                        f"Files downloaded: {final_file_count} files"
                    )
                    result = MockResult(1, output_text, error_message)
            else:
                # Generic failure case
                result = MockResult(1, output_text)
        else:
            # Use gallery-dl for other services
            cmd = [
                'gallery-dl',
                '--dest', output_dir,
                '--write-metadata',
                '--write-info-json',
                url
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=DOWNLOAD_TIMEOUT
            )
        
        # Count downloaded files
        file_count = 0
        if os.path.exists(output_dir):
            for root, dirs, files in os.walk(output_dir):
                file_count += len([f for f in files if f.lower().endswith(('.mp4', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp3', '.wav', '.pdf', '.txt', '.doc', '.docx', '.zip', '.rar', '.pptx', '.xlsx'))])
        
        success = result.returncode == 0
        output = result.stdout + "\n" + result.stderr if result.stderr else result.stdout
        
        return success, output, file_count, service_name
        
    except subprocess.TimeoutExpired:
        return False, f"Download timed out after {DOWNLOAD_TIMEOUT} seconds", 0, service_name
    except Exception as e:
        return False, f"Error: {str(e)}", 0, service_name

@app.route('/api/external_download', methods=['POST'])
def external_download():
    """Download from external services (Google Drive, GoFile, Bunkr)."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'})
        
        url = (data.get('url') or '').strip()
        destination = (data.get('destination') or '').strip() or None
        
        if not url:
            return jsonify({'success': False, 'error': 'URL is required'})
        
        # Validate URL format
        if not (url.startswith('http://') or url.startswith('https://')):
            return jsonify({'success': False, 'error': 'Invalid URL format'})
        
        # Check if URL is from supported services
        supported_domains = [
            'drive.google.com', 'docs.google.com',  # Google Drive (gdown)
            'gofile.io',                             # GoFile (gallery-dl)
            'bunkr.', 'bunkrr.',                     # Bunkr (gallery-dl)
            'imgur.com',                             # Imgur (gallery-dl)
            'catbox.moe',                            # Catbox (gallery-dl)
            'redgifs.com'                            # RedGifs (gallery-dl)
        ]
        if not any(domain in url for domain in supported_domains):
            return jsonify({
                'success': False, 
                'error': 'Unsupported service. Supported: Google Drive, GoFile, Bunkr, Imgur, Catbox, RedGifs'
            })
        
        # Generate download ID for tracking
        download_id = f"external_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        
        def download_thread():
            # Add to global queue
            add_to_global_queue(download_id)
            update_global_queue(download_id, status='downloading', current_file='Preparing external download...')
            
            # Create a progress callback function
            def progress_callback(files_processed, current_file_info):
                update_global_queue(download_id,
                                  status='downloading',
                                  files_downloaded=files_processed,
                                  current_file=current_file_info)
            
            try:
                success, output, file_count, service_name = perform_external_download(url, destination, progress_callback)
                
                final_status = 'completed' if success else 'failed'
                update_global_queue(download_id,
                                  status=final_status,
                                  total_files=file_count,
                                  files_downloaded=file_count,
                                  current_file=f'Downloaded {file_count} files from {service_name}',
                                  logs=output.split('\n') if output else [])
                
                if success:
                    print(f"External download completed: {service_name}, {file_count} files")
                else:
                    print(f"External download failed: {service_name}, {output}")
                    
            except Exception as e:
                update_global_queue(download_id,
                                  status='failed',
                                  current_file=f'Error: {str(e)}',
                                  logs=[str(e)])
                print(f"External download error: {e}")
        
        # Start download in background
        thread = threading.Thread(target=download_thread)
        thread.start()
        
        return jsonify({
            'success': True, 
            'message': 'External download started',
            'download_id': download_id
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': f'Failed to start download: {str(e)}'})

@app.route('/api/refresh_all_avatars', methods=['POST'])
def refresh_all_avatars():
    """Refresh avatars for all users."""
    def refresh_thread():
        # Register in download manager
        REFRESH_AVATARS_LABEL = 'Refresh Avatars'
        
        # Check if already running
        if REFRESH_AVATARS_LABEL in active_downloads:
            print("Avatar refresh already running")
            return

        add_to_global_queue(REFRESH_AVATARS_LABEL)
        
        conn = get_db_connection()
        users = conn.execute('SELECT username, platform FROM users ORDER BY username').fetchall()
        conn.close()
        
        success_count = 0
        total_count = len(users)
        
        update_global_queue(REFRESH_AVATARS_LABEL, 
                          status='running', 
                          total_files=total_count, 
                          files_downloaded=0, 
                          current_file='Initializing...')
        
        print(f"Starting avatar refresh for {total_count} users...")
        
        for i, user in enumerate(users):
            username = user['username']
            platform = user['platform']
            
            update_global_queue(REFRESH_AVATARS_LABEL, 
                              files_downloaded=i, 
                              current_file=f"Checking {username}...")
            
            try:
                # Remove existing avatar files (both new format and legacy format)
                for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif']:
                    # New format with platform prefix
                    old_path = os.path.join(AVATARS_PATH, f"{platform}_{username}{ext}")
                    if os.path.exists(old_path):
                        os.remove(old_path)
                    # Legacy format without platform prefix
                    old_path_legacy = os.path.join(AVATARS_PATH, f"{username}{ext}")
                    if os.path.exists(old_path_legacy):
                        os.remove(old_path_legacy)
                
                # Download new avatar
                local_avatar = download_avatar_with_gallery_dl(username, platform)
                
                if local_avatar:
                    success_count += 1
                    print(f"Avatar refreshed for {username} ({platform}) ({success_count}/{total_count})")
                else:
                    print(f"Failed to refresh avatar for {username} ({platform}) ({success_count}/{total_count})")
                
                # Add delay between requests to avoid rate limiting
                time.sleep(REQUEST_DELAY)
                
            except Exception as e:
                print(f"Error refreshing avatar for {username} ({platform}): {e}")
        
        # Mark completion
        update_global_queue(REFRESH_AVATARS_LABEL, 
                          status='completed', 
                          files_downloaded=total_count,
                          current_file=f"Completed ({success_count}/{total_count} refreshed)")
        
        print(f"Avatar refresh completed: {success_count}/{total_count} successful")
    
    thread = threading.Thread(target=refresh_thread)
    thread.start()
    
    return jsonify({
        'success': True, 
        'message': 'Avatar refresh started for all users'
    })

@app.route('/downloads/<path:filename>')
def download_file(filename):
    """Serve downloaded files."""
    file_path = os.path.join(DOWNLOADS_PATH, filename)
    if os.path.exists(file_path):
        return send_file(file_path)
    abort(404)

@app.route('/avatar/<username>')
def avatar(username):
    """Serve cached avatar for a user or 404 if not available."""
    # Get optional platform parameter
    platform = request.args.get('platform', '').lower()
    
    # Determine file by checking common extensions and platform-prefixed filenames
    for ext in ['.jpg', '.jpeg', '.png', '.webp']:
        # If platform is specified, prioritize that platform's avatar
        if platform in ('tiktok', 'instagram'):
            candidate = os.path.join(AVATARS_PATH, f"{platform}_{username}{ext}")
            if os.path.exists(candidate):
                return send_file(candidate)
        
        # Check all platform prefixes as fallback (for backward compatibility)
        for name in [f"{username}{ext}", f"tiktok_{username}{ext}", f"instagram_{username}{ext}"]:
            candidate = os.path.join(AVATARS_PATH, name)
            if os.path.exists(candidate):
                return send_file(candidate)
    abort(404)

@app.route('/api/download_zip/<username>')
def download_user_zip(username):
    """Create and download ZIP of user's content."""
    platform = request.args.get('platform','tiktok')
    zip_path = create_user_zip(username, platform)
    if zip_path and os.path.exists(zip_path):
        return send_file(zip_path, as_attachment=True, download_name=f"{username}_content.zip")
    abort(404)

# Template filters
@app.template_filter('min')
def min_filter(a, b):
    return min(a, b)

@app.template_filter('max')  
def max_filter(a, b):
    return max(a, b)

@app.template_filter('strftime')
def strftime_filter(value, fmt='%Y-%m-%d %H:%M'):
    try:
        if not value:
            return ''
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value)
            except Exception:
                return value
            return dt.strftime(fmt)
        return value.strftime(fmt)
    except Exception:
        return str(value)

# --- Telegram Bot Logic ---
def send_telegram_message(text):
    """Send a message via Telegram Bot."""
    if not TELEGRAM_AVAILABLE or not bot:
        return
    
    try:
        chat_id = get_setting('telegram_chat_id')
        if chat_id:
            bot.send_message(chat_id, text)
    except Exception as e:
        print(f"Telegram Send Error: {e}")

def run_bot_polling():
    """Run bot polling in a separate thread."""
    global bot
    try:
        print("Telegram Bot polling started...")
        bot.infinity_polling(interval=0, timeout=20)
    except Exception as e:
        print(f"Telegram Polling Error: {e}")

def start_telegram_bot():
    """Initialize and start the Telegram Bot."""
    global bot, bot_thread
    
    if not TELEGRAM_AVAILABLE:
        return

    token = get_setting('telegram_bot_token')
    if not token:
        print("Telegram Bot Token not set. Skipping bot startup.")
        return

    try:
        bot = telebot.TeleBot(token, threaded=False)

        # Defines commands
        @bot.message_handler(commands=['start', 'help'])
        def send_welcome(message):
            help_text = (
                "👋 *TrackUI Bot*\n\n"
                "Commands:\n"
                "/status - System stats\n"
                "/sync - Force synchronization\n"
                "/add <user> [platform] - Add user\n"
                "/delete <user> [platform] - Remove user\n"
                "/list - Browse all users\n"
                "/search <query> - Find users\n"
                "/logs - Show recent logs"
            )
            bot.reply_to(message, help_text, parse_mode='Markdown')

        @bot.message_handler(commands=['status'])
        def send_status(message):
            try:
                status_data = get_global_download_status()
                msg = f"📊 *System Status*\n"
                msg += f"Active Downloads: `{status_data['active_downloads']}`\n"
                msg += f"Completed: `{status_data['completed_downloads']}`\n"
                msg += f"Failed: `{status_data['failed_downloads']}`\n"
                bot.reply_to(message, msg, parse_mode='Markdown')
            except Exception as e:
                bot.reply_to(message, f"Error getting status: {e}")

        @bot.message_handler(commands=['sync'])
        def trigger_sync(message):
             bot.reply_to(message, "🚀 Sync triggered!")
             threading.Thread(target=run_sync_all_process).start()

        @bot.message_handler(commands=['add'])
        def add_user_command(message):
            try:
                parts = message.text.split()
                if len(parts) < 2:
                    bot.reply_to(message, "Usage: /add <username> [platform]\nDefault: tiktok")
                    return
                
                username = parts[1].strip().replace('@', '')
                platform = parts[2].lower() if len(parts) > 2 else 'tiktok'
                
                if platform not in ('tiktok', 'instagram', 'coomer'):
                    bot.reply_to(message, "Invalid platform. Use: tiktok, instagram, or coomer")
                    return

                conn = get_db_connection()
                existing = conn.execute('SELECT id FROM users WHERE username = ? AND platform = ?', (username, platform)).fetchone()
                if existing:
                    conn.close()
                    bot.reply_to(message, f"User {username} already exists on {platform}.")
                    return

                conn.execute('INSERT INTO users (username, platform, display_name, is_tracking) VALUES (?, ?, ?, 1)', (username, platform, username))
                conn.commit()
                conn.close()
                
                bot.reply_to(message, f"✅ Added {username} ({platform}). Syncing metadata...")
                
                def sync_new():
                    update_user_stats(username, platform)
                threading.Thread(target=sync_new).start()
                
            except Exception as e:
                bot.reply_to(message, f"Error adding user: {e}")

        @bot.message_handler(commands=['delete', 'remove'])
        def delete_user_command(message):
            try:
                parts = message.text.split()
                if len(parts) < 2:
                    bot.reply_to(message, "Usage: /delete <username> [platform]")
                    return
                
                username = parts[1].strip().replace('@', '')
                platform = parts[2].lower() if len(parts) > 2 else 'tiktok'
                
                conn = get_db_connection()
                user = conn.execute('SELECT id FROM users WHERE username = ? AND platform = ?', (username, platform)).fetchone()
                
                if not user:
                    conn.close()
                    bot.reply_to(message, f"❌ User {username} ({platform}) not found.")
                    return
                
                # Delete from user_tags and users
                conn.execute('DELETE FROM user_tags WHERE user_id = ?', (user['id'],))
                conn.execute('DELETE FROM users WHERE id = ?', (user['id'],))
                conn.commit()
                conn.close()
                
                bot.reply_to(message, f"🗑️ Removed {username} ({platform}) from tracking list.")
                
            except Exception as e:
                bot.reply_to(message, f"Error removing user: {e}")

        @bot.message_handler(commands=['search'])
        def search_users_command(message):
            try:
                parts = message.text.split(maxsplit=1)
                if len(parts) < 2:
                    bot.reply_to(message, "Usage: /search <query>")
                    return
                
                query = parts[1].strip()
                match = f"%{query}%"
                
                conn = get_db_connection()
                users = conn.execute('SELECT username, platform FROM users WHERE username LIKE ? ORDER BY id DESC LIMIT 20', (match,)).fetchall()
                conn.close()
                
                if not users:
                    bot.reply_to(message, f"No users found matching '{query}'.")
                    return
                
                markup = telebot.types.InlineKeyboardMarkup()
                for u in users:
                    btn_text = f"{u['username']} ({u['platform']})"
                    markup.add(telebot.types.InlineKeyboardButton(
                        text=btn_text, 
                        callback_data=f"view:{u['username']}:{u['platform']}"
                    ))
                
                bot.reply_to(message, f"🔍 Found {len(users)} matches for '{query}':", reply_markup=markup)
            except Exception as e:
                bot.reply_to(message, f"Error searching users: {e}")

        def get_paginated_markup(users, page=1, per_page=10):
            """Helper to generate paginated user list."""
            total_users = len(users)
            total_pages = (total_users + per_page - 1) // per_page
            
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            current_users = users[start_idx:end_idx]
            
            markup = telebot.types.InlineKeyboardMarkup()
            
            for u in current_users:
                btn_text = f"{u['username']} ({u['platform']})"
                markup.add(telebot.types.InlineKeyboardButton(
                    text=btn_text, 
                    callback_data=f"view:{u['username']}:{u['platform']}:{page}"
                ))
            
            # Navigation buttons
            nav_row = []
            if page > 1:
                nav_row.append(telebot.types.InlineKeyboardButton("⬅️ Prev", callback_data=f"list_page:{page-1}"))
            
            nav_row.append(telebot.types.InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="noop"))
            
            if page < total_pages:
                nav_row.append(telebot.types.InlineKeyboardButton("Next ➡️", callback_data=f"list_page:{page+1}"))
            
            if nav_row:
                markup.row(*nav_row)
            
            msg_text = f"📋 *Tracked Users ({total_users})*\nPage {page}/{total_pages}\nTap a user to see their profile picture."
            return msg_text, markup

        @bot.message_handler(commands=['list'])
        def list_users_command(message):
            try:
                conn = get_db_connection()
                users = conn.execute('SELECT username, platform FROM users ORDER BY id DESC').fetchall()
                conn.close()
                
                if not users:
                    bot.reply_to(message, "No users tracked yet.")
                    return
                
                msg_text, markup = get_paginated_markup(users, page=1)
                bot.reply_to(message, msg_text, parse_mode='Markdown', reply_markup=markup)
            except Exception as e:
                bot.reply_to(message, f"Error listing users: {e}")

        @bot.callback_query_handler(func=lambda call: True)
        def callback_handler(call):
            try:
                if call.data == "noop":
                    bot.answer_callback_query(call.id)
                    return

                if call.data.startswith('back_to_list:'):
                    try:
                        page = int(call.data.split(':')[1])
                        # Delete current photo message
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                        
                        # Send list message
                        conn = get_db_connection()
                        users = conn.execute('SELECT username, platform FROM users ORDER BY id DESC').fetchall()
                        conn.close()
                        
                        if users:
                            msg_text, markup = get_paginated_markup(users, page=page)
                            bot.send_message(call.message.chat.id, msg_text, parse_mode='Markdown', reply_markup=markup)
                        else:
                            bot.send_message(call.message.chat.id, "No users tracked.")
                            
                    except Exception as e:
                        print(f"Back to list error: {e}")
                        bot.send_message(call.message.chat.id, "Error returning to list.")
                    
                    bot.answer_callback_query(call.id)
                    return

                if call.data.startswith('list_page:'):
                    page = int(call.data.split(':')[1])
                    conn = get_db_connection()
                    users = conn.execute('SELECT username, platform FROM users ORDER BY id DESC').fetchall()
                    conn.close()
                    
                    msg_text, markup = get_paginated_markup(users, page=page)
                    
                    try:
                        bot.edit_message_text(
                            chat_id=call.message.chat.id,
                            message_id=call.message.message_id,
                            text=msg_text,
                            parse_mode='Markdown',
                            reply_markup=markup
                        )
                    except Exception:
                        pass # Message might not have changed
                    
                    bot.answer_callback_query(call.id)
                    return

                if call.data.startswith('view:'):
                    parts = call.data.split(':')
                    # Handle both old format (3 parts) and new format (4 parts with page)
                    if len(parts) >= 3:
                        username = parts[1]
                        platform = parts[2]
                        page = int(parts[3]) if len(parts) > 3 else 1
                    else:
                        bot.answer_callback_query(call.id, "Error: Invalid data")
                        return

                    # Delete the list message first
                    try:
                        bot.delete_message(call.message.chat.id, call.message.message_id)
                    except Exception:
                        pass
                    
                    # Try to find avatar
                    found_path = None
                    for ext in ['.jpg', '.jpeg', '.png', '.webp']:
                        # Try platform specific first
                        candidates = [
                            f"{platform}_{username}{ext}",
                            f"{username}{ext}",
                            f"tiktok_{username}{ext}",
                            f"instagram_{username}{ext}"
                        ]
                        
                        for candidate in candidates:
                            full_path = os.path.join(AVATARS_PATH, candidate)
                            if os.path.exists(full_path):
                                found_path = full_path
                                break
                        if found_path: break
                    
                    # Back button markup
                    markup = telebot.types.InlineKeyboardMarkup()
                    markup.add(telebot.types.InlineKeyboardButton("⬅️ Back to List", callback_data=f"back_to_list:{page}"))

                    if found_path:
                        with open(found_path, 'rb') as photo:
                            bot.send_photo(
                                call.message.chat.id, 
                                photo, 
                                caption=f"👤 *{username}* ({platform})", 
                                parse_mode='Markdown',
                                reply_markup=markup
                            )
                        bot.answer_callback_query(call.id)
                    else:
                        # Not found locally - try to download
                        bot.answer_callback_query(call.id, "Downloading avatar, please wait...")
                        
                        try:
                            # Attempt download
                            new_avatar = download_avatar_with_gallery_dl(username, platform)
                            if new_avatar and os.path.exists(new_avatar):
                                with open(new_avatar, 'rb') as photo:
                                    bot.send_photo(
                                        call.message.chat.id, 
                                        photo, 
                                        caption=f"👤 *{username}* ({platform})", 
                                        parse_mode='Markdown',
                                        reply_markup=markup
                                    )
                            else:
                                bot.send_message(call.message.chat.id, f"⚠️ Could not download avatar for {username}.", reply_markup=markup)
                        except Exception as e:
                            print(f"On-demand download error: {e}")
                            bot.send_message(call.message.chat.id, f"⚠️ Error downloading avatar: {e}", reply_markup=markup)
                        except Exception as e:
                            print(f"On-demand download error: {e}")
                            bot.send_message(call.message.chat.id, f"⚠️ Error downloading avatar: {e}")
                            
            except Exception as e:
                print(f"Callback error: {e}")
                bot.answer_callback_query(call.id, "Error handling request.")

        @bot.message_handler(commands=['logs'])
        def get_logs_command(message):
            try:
                if not sync_logs:
                    bot.reply_to(message, "No logs available.")
                    return
                
                recent_logs = sync_logs[-15:]
                log_text = "\n".join(recent_logs)
                # Telegram message limit is 4096 chars
                if len(log_text) > 4000:
                    log_text = log_text[-4000:]
                
                bot.reply_to(message, f"📝 *Recent Logs*\n```\n{log_text}\n```", parse_mode='Markdown')
            except Exception as e:
                bot.reply_to(message, f"Error fetching logs: {e}")

        # Start polling thread
        if bot_thread is None or not bot_thread.is_alive():
            bot_thread = threading.Thread(target=run_bot_polling, daemon=True)
            bot_thread.start()
            print("Telegram Bot thread initialized.")

    except Exception as e:
        print(f"Failed to start Telegram Bot: {e}")

if __name__ == '__main__':
    # Initialize database on startup
    init_database()
    
    # Verify database is working
    if not verify_database():
        print("Database verification failed! Exiting.")
        exit(1)
    
    # Ensure data directories exist
    os.makedirs(DOWNLOADS_PATH, exist_ok=True)
    os.makedirs(AVATARS_PATH, exist_ok=True)
    
    print("TrackUI starting...")
    print(f"Downloads will be saved to: {os.path.abspath(DOWNLOADS_PATH)}")
    
    # Tests gallery-dl availability
    success, message = test_tiktok_access()
    print(f"Gallery-dl status: {message}")

    # Start scheduler thread
    start_scheduler_thread()
    
    # Initialize Telegram Bot (if configured)
    start_telegram_bot()
    
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=7777)
