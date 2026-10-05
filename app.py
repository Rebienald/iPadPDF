#!/usr/bin/env python3
"""
iPadPDF - Ultra-Lightweight Pure Monochrome (Grayscale) Single Page App
Optimized for iPad 1st Gen (iOS 5, 256MB RAM):
- Pure black, white, and gray minimalist interface (zero colors)
- Pages rendered in 8-bit grayscale JPEG (saves ~40% file size and RAM)
- Single Page Application (SPA): no page reloads, no broken offline refreshes
- Swipe left/right & edge-tap navigation
- LocalStorage state persistence
"""

import os
import sys
import time
import socket
import sqlite3
import subprocess
import shutil
import json
import gzip
import threading
from pathlib import Path
from flask import Flask, request, redirect, url_for, send_file, render_template_string, abort, jsonify
import pypdfium2 as pdfium

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / 'uploads'
CACHE_DIR = BASE_DIR / 'cache'
DATA_DIR = BASE_DIR / 'data'
DB_PATH = DATA_DIR / 'library.db'

# Load environment variables from local .env if present
env_file = BASE_DIR / '.env'
if env_file.exists():
    try:
        with open(env_file, 'r', encoding='utf-8') as ef:
            for line in ef:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    k = k.strip()
                    v = v.strip().strip('\'"')
                    if k and k not in os.environ:
                        os.environ[k] = v
    except Exception:
        pass

for d in (UPLOADS_DIR, CACHE_DIR, DATA_DIR):
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 250 * 1024 * 1024  # 250 MB max PDF upload

@app.after_request
def compress_response(response):
    accept_encoding = request.headers.get('Accept-Encoding', '')
    if 'gzip' not in accept_encoding.lower():
        return response
    if response.status_code < 200 or response.status_code >= 300:
        return response
    if 'Content-Encoding' in response.headers:
        return response
    content_type = response.headers.get('Content-Type', '')
    if any(t in content_type for t in ['text/html', 'application/json', 'text/css', 'application/javascript']):
        data = response.get_data()
        if len(data) > 200:
            compressed = gzip.compress(data, compresslevel=6)
            response.set_data(compressed)
            response.headers['Content-Encoding'] = 'gzip'
            response.headers['Content-Length'] = len(compressed)
    return response

import urllib.request
import urllib.error

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    with conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                filename TEXT NOT NULL,
                page_count INTEGER NOT NULL DEFAULT 1,
                filesize INTEGER NOT NULL DEFAULT 0,
                current_page INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS quizzes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                q_count INTEGER NOT NULL DEFAULT 5,
                quiz_data TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(book_id, q_count)
            )
        ''')
    conn.close()

init_db()

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

def count_pages(pdf_path):
    try:
        doc = pdfium.PdfDocument(str(pdf_path))
        count = len(doc)
        doc.close()
        return max(1, count)
    except Exception as e:
        app.logger.error(f'Error counting pages: {e}')
        return 1

def render_page_to_jpeg(pdf_path, page_num, output_jpg, dpi=160, quality=88):
    output_jpg = Path(output_jpg)
    if output_jpg.exists():
        return True

    # Method 1: Native pdftoppm in ultra-clear grayscale at 160 DPI
    if shutil.which('pdftoppm'):
        prefix = output_jpg.with_suffix('')
        cmd = [
            'pdftoppm',
            '-gray',
            '-jpeg',
            '-jpegopt', f'quality={quality}',
            '-r', str(dpi),
            '-f', str(page_num),
            '-l', str(page_num),
            '-singlefile',
            str(pdf_path),
            str(prefix)
        ]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0 and output_jpg.exists():
            return True

    # Method 2: Fallback to portable pypdfium2 in crisp grayscale
    try:
        doc = pdfium.PdfDocument(str(pdf_path))
        if page_num < 1 or page_num > len(doc):
            doc.close()
            return False
        page = doc[page_num - 1]
        scale = dpi / 72.0
        pil_img = page.render(scale=scale).to_pil().convert('L')
        pil_img.save(str(output_jpg), 'JPEG', quality=quality)
        doc.close()
        return True
    except Exception as e:
        app.logger.error(f'Failed rendering page {page_num}: {e}')
        return False

def sync_uploaded_files_to_db():
    try:
        conn = get_db()
        existing_files = {row['filename'] for row in conn.execute('SELECT filename FROM books').fetchall()}
        added = False
        for pdf_file in sorted(UPLOADS_DIR.glob('*.pdf')):
            if pdf_file.name not in existing_files:
                clean_title = pdf_file.stem
                parts = clean_title.split('_', 1)
                if len(parts) == 2 and parts[0].isdigit():
                    clean_title = parts[1]
                clean_title = clean_title.replace('_', ' ').replace('-', ' ').strip()
                page_count = count_pages(pdf_file)
                filesize = pdf_file.stat().st_size
                with conn:
                    conn.execute('''
                        INSERT INTO books (title, filename, page_count, filesize, current_page)
                        VALUES (?, ?, ?, ?, 1)
                    ''', (clean_title, pdf_file.name, page_count, filesize))
                added = True
        conn.close()
        if added:
            app.logger.info("Auto-registered untracked PDFs found in uploads directory")
    except Exception as e:
        app.logger.error(f"Error syncing uploaded files to db: {e}")

sync_uploaded_files_to_db()

def sync_git_repo(commit_message):
    try:
        token = os.environ.get('GITHUB_TOKEN', '').strip()
        if token:
            subprocess.run(['git', 'config', 'user.email', 'ipadpdf@noreply.github.com'], cwd=str(BASE_DIR), capture_output=True)
            subprocess.run(['git', 'config', 'user.name', 'iPadPDF Server'], cwd=str(BASE_DIR), capture_output=True)
            remote_url = f"https://oauth2:{token}@github.com/Rebienald/iPadPDF.git"
            subprocess.run(['git', 'remote', 'set-url', 'origin', remote_url], cwd=str(BASE_DIR), capture_output=True)

        subprocess.run(['git', 'add', '-A', 'uploads', 'data'], cwd=str(BASE_DIR), capture_output=True)
        diff_check = subprocess.run(['git', 'diff', '--staged', '--name-only'], cwd=str(BASE_DIR), capture_output=True, text=True)
        if diff_check.stdout.strip():
            subprocess.run(['git', 'commit', '-m', f"Auto-sync: {commit_message} [skip render]"], cwd=str(BASE_DIR), capture_output=True)
            subprocess.run(['git', 'push', 'origin', 'main'], cwd=str(BASE_DIR), capture_output=True)
            app.logger.info(f"Auto-sync pushed to GitHub: {commit_message}")
    except Exception as e:
        app.logger.error(f"Git auto-sync error: {e}")

def trigger_git_sync(commit_message):
    t = threading.Thread(target=sync_git_repo, args=(commit_message,))
    t.daemon = True
    t.start()

# ==================== AI ENGINE (GEMINI WITH ROTATION & BACKUPS) ====================
def get_api_keys():
    keys = []
    primary = os.environ.get('GEMINI_API_KEY', '').strip()
    if primary:
        keys.append(primary)
    backups = os.environ.get('GEMINI_BACKUP_KEYS', '').strip()
    if backups:
        for k in backups.split(','):
            k = k.strip()
            if k and k not in keys:
                keys.append(k)
    return keys

def extract_pdf_text(pdf_path, max_chars=100000):
    text = ""
    # Extract complete PDF text using poppler pdftotext
    if shutil.which('pdftotext'):
        try:
            cmd = ['pdftotext', str(pdf_path), '-']
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=12)
            if res.returncode == 0 and res.stdout.strip():
                text = res.stdout.strip()
        except Exception:
            pass
    # Fallback to pdfium for entire document
    if not text:
        try:
            doc = pdfium.PdfDocument(str(pdf_path))
            parts = []
            for p in range(len(doc)):
                page = doc[p]
                tp = page.get_textpage()
                parts.append(tp.get_text_range())
            doc.close()
            text = "\n".join(parts).strip()
        except Exception:
            pass
    return text[:max_chars]

def call_gemini(prompt, response_mime="text/plain", timeout=30):
    payload = {
        'contents': [{'parts': [{'text': prompt}]}]
    }
    if response_mime == "application/json":
        payload['generationConfig'] = {'responseMimeType': 'application/json'}

    models = ['gemini-3.5-flash-lite', 'gemini-3.1-flash-lite', 'gemini-flash-latest']
    api_keys = get_api_keys()
    if not api_keys:
        app.logger.warning('No Gemini API keys configured.')
        return None

    for key in api_keys:
        for m in models:
            url = f'https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={key}'
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as res:
                    data = json.loads(res.read().decode('utf-8'))
                    return data['candidates'][0]['content']['parts'][0]['text']
            except Exception as e:
                app.logger.warning(f'Gemini key/model {m} failed: {e}')
                continue
    return None

def generate_quiz_for_pdf(pdf_path, title, count=5):
    text = extract_pdf_text(pdf_path)
    if not text or len(text.strip()) < 50:
        text = f"Study document titled {title}."

    prompt = f'''Generate exactly {count} multiple-choice quiz questions based on the document below.
Requirements:
1. Distribute questions evenly across the ENTIRE document from the first section to the final section.
2. 4 choices per question (options).
3. Set answer to index (0, 1, 2, or 3) of correct option.
4. Keep explanation to 1 concise sentence.
5. Return ONLY a valid JSON list of {count} objects.

DOCUMENT:
{text}

JSON FORMAT:
[
  {{
    "question": "Question text",
    "options": ["Option A", "Option B", "Option C", "Option D"],
    "answer": 0,
    "explanation": "Brief 1-sentence reason."
  }}
]'''

    raw = call_gemini(prompt, response_mime="application/json", timeout=30)
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and len(parsed) > 0:
                return parsed
        except Exception:
            pass
    return None

# Pure monochrome minimalist single page template
SPA_HTML = '''<!DOCTYPE html>
<html manifest="/offline.appcache">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black">
    <link rel="apple-touch-icon" href="/icon.png">
    <title>PDF</title>
    <style>
    * {
        -webkit-box-sizing: border-box;
        box-sizing: border-box;
        border-radius: 0 !important;
        -webkit-touch-callout: none !important;
        -webkit-user-select: none !important;
        -khtml-user-select: none !important;
        -moz-user-select: none !important;
        -ms-user-select: none !important;
        user-select: none !important;
        -webkit-tap-highlight-color: rgba(0,0,0,0) !important;
    }
    input, textarea {
        -webkit-touch-callout: default !important;
        -webkit-user-select: text !important;
        user-select: text !important;
    }
    body, html {
        margin: 0;
        padding: 0;
        width: 100%;
        min-height: 100%;
        font-family: -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif;
        background-color: #ffffff;
        color: #000000;
        -webkit-overflow-scrolling: touch;
        overflow-x: hidden;
    }

    /* Monochrome Library View */
    #libraryView {
        background-color: #ffffff;
        color: #000000;
        padding: 14px;
        font-size: 15px;
        line-height: 1.4;
    }
    .box {
        background: #ffffff;
        border: 1px solid #000000;
        padding: 12px;
        margin-bottom: 12px;
    }
    .box-title {
        font-size: 16px;
        font-weight: bold;
        margin: 0 0 8px 0;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .btn {
        display: inline-block;
        background: #000000;
        color: #ffffff !important;
        border: 1px solid #000000;
        padding: 8px 14px;
        font-size: 14px;
        font-weight: bold;
        cursor: pointer;
        text-align: center;
        text-decoration: none;
        -webkit-appearance: none;
    }
    .btn-light {
        background: #ffffff;
        color: #000000 !important;
        border: 1px solid #000000;
    }
    .book-table {
        width: 100%;
        border-collapse: collapse;
        border: 1px solid #000000;
        margin-top: 8px;
    }
    .book-table th, .book-table td {
        padding: 10px 8px;
        border: 1px solid #cccccc;
        text-align: left;
        vertical-align: middle;
    }
    .book-table th {
        background: #f0f0f0;
        border-bottom: 2px solid #000000;
        font-size: 13px;
        text-transform: uppercase;
    }
    input[type="file"] {
        font-size: 15px;
        padding: 6px 0;
        margin-bottom: 10px;
        display: block;
    }

    /* Monochrome Fullscreen Reader View */
    #readerView {
        display: none;
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        width: 100%;
        height: 100%;
        background-color: #000000;
        color: #ffffff;
        overflow: hidden;
        z-index: 10;
    }
    #topBar {
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        height: 44px;
        background: #000000;
        border-bottom: 1px solid #444444;
        padding: 5px 10px;
        z-index: 999;
        font-size: 14px;
        box-sizing: border-box;
    }
    .nav-btn {
        color: #ffffff !important;
        text-decoration: none;
        font-weight: bold;
        float: left;
        line-height: 32px;
        font-size: 14px;
        padding: 0 12px;
        background: #222222;
        border: 1px solid #666666;
        border-radius: 4px;
        margin-right: 10px;
        cursor: pointer;
        display: inline-block;
    }
    .page-badge {
        float: right;
        color: #ffffff;
        font-size: 14px;
        line-height: 32px;
        font-weight: bold;
        letter-spacing: 1px;
    }
    #pageArea {
        position: absolute;
        top: 44px;
        bottom: 0;
        left: 0;
        right: 0;
        overflow-y: scroll;
        overflow-x: hidden;
        -webkit-overflow-scrolling: touch;
        padding-top: 10px;
        padding-bottom: 40px;
        text-align: center;
        background-color: #000000;
        width: 100%;
    }
    .pdf-page-container {
        text-align: center;
        margin: 0 auto 14px auto;
        background: #000000;
        min-height: 500px;
    }
    .pdf-page {
        display: block;
        margin: 0 auto;
        max-width: 100%;
        height: auto;
        background: #ffffff;
        border: 1px solid #333333;
    }
    #cacheStatus {
        display: none;
        background: #000000;
        border-bottom: 1px solid #ffffff;
        color: #ffffff;
        text-align: center;
        font-size: 13px;
        padding: 6px 10px;
        position: fixed;
        top: 38px;
        left: 0;
        right: 0;
        z-index: 998;
    }
    </style>
</head>
<body>

    <!-- ==================== 1. MONOCHROME LIBRARY VIEW ==================== -->
    <div id="libraryView">
        <div class="box" style="overflow: hidden;">
            <button onclick="forceRefresh()" class="btn" style="float: right; margin-left: 10px; padding: 8px 18px; font-size: 14px; font-weight: bold; cursor: pointer;">&#8635; Refresh</button>
            <div class="box-title" style="margin-top: 2px;">iPad PDF Library</div>
            <div>Address: <strong>http://{{ local_ip }}:{{ port }}</strong></div>
        </div>

        <div class="box">
            <div class="box-title">Offline Cache</div>
            <div style="margin-bottom: 10px; font-size: 13px; color: #444444;">
                Download all documents into iPad storage for offline reading:
            </div>
            <button onclick="startOfflineCacheAll()" id="libCacheBtn" class="btn" style="padding: 10px 18px;">Save All Books for Offline</button>
            <div id="libCacheStatus" style="margin-top: 8px; font-size: 13px; font-weight: bold;"></div>
        </div>

        <div class="box">
            <div class="box-title">Upload Document</div>
            <form action="/upload" method="post" enctype="multipart/form-data">
                <input type="file" name="pdf_file" accept=".pdf,application/pdf" required>
                <input type="submit" value="Upload" class="btn">
            </form>
        </div>

        <div class="box">
            <div class="box-title">Documents ({{ books|length }})</div>
            {% if books %}
            <table class="book-table">
                <thead>
                    <tr>
                        <th>Title</th>
                        <th>Pages</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>
                    {% for b in books %}
                    <tr>
                        <td>
                            <a href="javascript:void(0)" onclick="openBook({{ b.id }})" style="color: #000000; font-weight: bold; text-decoration: underline;">{{ b.title }}</a>
                            <div style="font-size: 12px; color: #555555;" id="progress_{{ b.id }}">Page {{ b.current_page }} of {{ b.page_count }}</div>
                        </td>
                        <td>{{ b.page_count }}</td>
                        <td style="white-space: nowrap;">
                            <button onclick="openBook({{ b.id }})" class="btn" style="padding: 6px 12px;">Open</button>
                            <a href="/quiz/{{ b.id }}" class="btn" style="padding: 6px 10px; margin-left: 4px;">Quiz</a>
                            <form action="/delete/{{ b.id }}" method="post" style="display: inline; margin-left: 4px;" onsubmit="return confirm('Delete this PDF?');">
                                <input type="submit" value="Del" class="btn btn-light" style="padding: 6px 8px;">
                            </form>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <p style="color: #555555; margin: 0;">No documents uploaded.</p>
            {% endif %}
        </div>
    </div>


    <!-- ==================== 2. MONOCHROME READER VIEW ==================== -->
    <div id="readerView">
        <div id="topBar">
            <a href="javascript:void(0)" onclick="closeBook()" class="nav-btn">&larr; Library</a>
            <select id="readerBookSelect" onchange="openBook(this.value)" style="background: #222222; color: #ffffff; border: 1px solid #666666; border-radius: 4px; padding: 4px 8px; font-size: 14px; height: 32px; max-width: 185px; float: left; margin-top: 0px; -webkit-appearance: menulist;">
                {% for b in books %}
                <option value="{{ b.id }}">{{ b.title }} ({{ b.page_count }}p)</option>
                {% endfor %}
            </select>
            <a href="javascript:void(0)" onclick="goToQuiz()" class="nav-btn" style="margin-left: 6px;">Quiz</a>
            <a href="javascript:void(0)" onclick="startOfflineCacheAll()" class="nav-btn" id="readerCacheBtn" style="margin-left: 6px; font-size: 13px; font-weight: normal; color: #cccccc !important;">Save All</a>
            <span class="page-badge" id="pageDisplay">1 / 1</span>
        </div>
        <div id="cacheStatus"></div>

        <div id="pageArea"></div>

        <!-- Minimalist Chat Floating Button & Modal -->
        <div id="chatFloatBtn" onclick="toggleChat()" style="position: absolute; bottom: 20px; right: 20px; width: 44px; height: 44px; line-height: 42px; text-align: center; background: #000000; color: #ffffff; border: 2px solid #ffffff; border-radius: 22px; font-size: 20px; font-weight: bold; cursor: pointer; z-index: 999; -webkit-box-shadow: 0 0 6px rgba(255,255,255,0.4); box-shadow: 0 0 6px rgba(255,255,255,0.4);">?</div>

        <div id="chatModal" style="display: none; position: absolute; top: 48px; left: 8px; right: 8px; max-width: 520px; margin: 0 auto; background: #000000; border: 2px solid #ffffff; border-radius: 6px; z-index: 1000; color: #ffffff; padding: 10px 12px; font-family: -apple-system, Helvetica, Arial, sans-serif; -webkit-box-shadow: 0 4px 16px rgba(0,0,0,0.9); box-shadow: 0 4px 16px rgba(0,0,0,0.9);">
            <div style="overflow: hidden; padding-bottom: 6px; border-bottom: 1px solid #333333; margin-bottom: 8px;">
                <span style="font-size: 14px; font-weight: bold; letter-spacing: 1px; float: left; line-height: 28px;">DOCUMENT CHAT</span>
                <button onclick="closeChat()" style="float: right; padding: 4px 12px; font-size: 13px; font-weight: bold; margin-left: 6px; cursor: pointer; background: #ffffff !important; color: #000000 !important; border: 1px solid #ffffff; border-radius: 3px; -webkit-appearance: none;">Close &times;</button>
                <button onclick="clearChatHistory()" style="float: right; padding: 4px 10px; font-size: 12px; font-weight: bold; cursor: pointer; background: #000000 !important; color: #ffffff !important; border: 1px solid #888888; border-radius: 3px; -webkit-appearance: none;">Clear</button>
            </div>
            <table style="width: 100%; border-collapse: collapse; border-spacing: 0; margin-bottom: 8px;">
                <tr>
                    <td style="padding: 0 6px 0 0;">
                        <input type="text" id="chatInput" placeholder="Ask a question..." onkeydown="if(event.keyCode===13)sendChatMessage();" style="width: 100%; box-sizing: border-box; -webkit-box-sizing: border-box; background: #222222; color: #ffffff; border: 1px solid #666666; padding: 8px; font-size: 14px; border-radius: 3px; -webkit-appearance: none;">
                    </td>
                    <td style="width: 60px; padding: 0;">
                        <button id="chatSendBtn" onclick="sendChatMessage()" style="width: 100%; background: #ffffff !important; color: #000000 !important; font-weight: bold; border: 1px solid #ffffff; padding: 8px 0; font-size: 14px; border-radius: 3px; cursor: pointer; -webkit-appearance: none;">Ask</button>
                    </td>
                </tr>
            </table>
            <div id="chatMessages" style="height: 180px; max-height: 200px; overflow-y: auto; -webkit-overflow-scrolling: touch; font-size: 13px; line-height: 1.4; border: 1px solid #222222; padding: 6px; background: #111111; border-radius: 3px;">
                <div style="color: #888888; font-style: italic;">Ask any question about this document.</div>
            </div>
        </div>
    </div>


    <!-- ==================== 3. SCRIPT (ES5 Monochrome Offline SPA) ==================== -->
    <script>
    var allBooks = {{ books_json|safe }};
    var currentBook = null;
    var currentPage = 1;

    function forceRefresh() {
        try {
            localStorage.removeItem('ipad_active_book');
        } catch(e) {}
        window.location.href = '/?refresh=' + new Date().getTime();
    }

    function getBook(id) {
        for (var i = 0; i < allBooks.length; i++) {
            if (allBooks[i].id == id) return allBooks[i];
        }
        return null;
    }

    function openBook(id, page) {
        var b = getBook(id);
        if (!b) return;
        currentBook = b;

        var targetPage = 1;
        if (page) {
            targetPage = page;
        } else {
            try {
                var saved = localStorage.getItem('page_' + b.id);
                targetPage = saved ? parseInt(saved, 10) : (b.current_page || 1);
            } catch(e) {
                targetPage = b.current_page || 1;
            }
        }
        targetPage = Math.max(1, Math.min(targetPage, b.page_count));

        try {
            localStorage.setItem('ipad_active_book', b.id);
            localStorage.setItem('page_' + b.id, targetPage);
        } catch(e) {}

        document.body.style.backgroundColor = '#000000';
        document.body.style.overflow = 'hidden';
        document.getElementById('libraryView').style.display = 'none';
        document.getElementById('readerView').style.display = 'block';
        document.getElementById('readerBookSelect').value = b.id;

        renderContinuousPages(targetPage);
    }

    function closeChat() {
        var modal = document.getElementById('chatModal');
        var btn = document.getElementById('chatFloatBtn');
        if (modal) modal.style.display = 'none';
        if (btn) btn.style.display = 'block';
    }

    function closeBook() {
        try {
            localStorage.removeItem('ipad_active_book');
        } catch(e) {}
        closeChat();
        document.body.style.backgroundColor = '#ffffff';
        document.body.style.overflow = 'auto';
        document.getElementById('pageArea').innerHTML = '';
        document.getElementById('readerView').style.display = 'none';
        document.getElementById('libraryView').style.display = 'block';
        window.scrollTo(0, 0);
    }

    function toggleChat() {
        var modal = document.getElementById('chatModal');
        var btn = document.getElementById('chatFloatBtn');
        if (!modal) return;
        if (modal.style.display === 'none' || modal.style.display === '') {
            modal.style.display = 'block';
            if (btn) btn.style.display = 'none';
            var inp = document.getElementById('chatInput');
            if (inp) {
                setTimeout(function() { inp.focus(); }, 60);
            }
        } else {
            closeChat();
        }
    }

    function clearChatHistory() {
        var box = document.getElementById('chatMessages');
        if (box) {
            box.innerHTML = '<div style="color: #888888; font-style: italic;">Ask any question about this document.</div>';
        }
    }

    function sendChatMessage() {
        var inp = document.getElementById('chatInput');
        var btn = document.getElementById('chatSendBtn');
        var box = document.getElementById('chatMessages');
        if (!inp || !box || !currentBook) return;
        var q = inp.value.trim ? inp.value.trim() : inp.value.replace(/^\s+|\s+$/g, '');
        if (!q) return;

        var userDiv = document.createElement('div');
        userDiv.style.margin = '4px 0';
        userDiv.style.textAlign = 'right';
        userDiv.innerHTML = '<span style="display: inline-block; background: #333333; color: #ffffff; padding: 4px 8px; border-radius: 4px; max-width: 85%; text-align: left; word-wrap: break-word;">' + q.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</span>';
        box.appendChild(userDiv);

        inp.value = '';
        inp.disabled = true;
        if (btn) btn.disabled = true;

        var statusDiv = document.createElement('div');
        statusDiv.style.margin = '4px 0';
        statusDiv.style.color = '#aaaaaa';
        statusDiv.style.fontStyle = 'italic';
        statusDiv.innerText = 'Thinking...';
        box.appendChild(statusDiv);
        box.scrollTop = box.scrollHeight;

        var xhr = new XMLHttpRequest();
        xhr.open('POST', '/chat/' + currentBook.id, true);
        xhr.setRequestHeader('Content-Type', 'application/json');
        xhr.onreadystatechange = function() {
            if (xhr.readyState === 4) {
                inp.disabled = false;
                if (btn) btn.disabled = false;
                if (statusDiv && statusDiv.parentNode) {
                    statusDiv.parentNode.removeChild(statusDiv);
                }
                var ans = 'Error: could not get an answer.';
                if (xhr.status === 200) {
                    try {
                        var res = JSON.parse(xhr.responseText);
                        if (res.answer) ans = res.answer;
                        else if (res.error) ans = 'Error: ' + res.error;
                    } catch(e) {
                        ans = xhr.responseText || 'Error parsing response.';
                    }
                } else {
                    try {
                        var errRes = JSON.parse(xhr.responseText);
                        if (errRes.error) ans = 'Error: ' + errRes.error;
                    } catch(e) {}
                }

                var botDiv = document.createElement('div');
                botDiv.style.margin = '4px 0';
                botDiv.style.textAlign = 'left';
                var cleanAns = ans.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').split(String.fromCharCode(10)).join('<br>');
                botDiv.innerHTML = '<span style="display: inline-block; background: #000000; border: 1px solid #444444; color: #ffffff; padding: 4px 8px; border-radius: 4px; max-width: 90%; text-align: left; word-wrap: break-word;">' + cleanAns + '</span>';
                box.appendChild(botDiv);
                box.scrollTop = box.scrollHeight;
            }
        };
        xhr.send(JSON.stringify({ question: q }));
    }

    function goToQuiz() {
        if (currentBook) {
            window.location.href = '/quiz/' + currentBook.id;
        }
    }

    function renderContinuousPages(targetPage) {
        if (!currentBook) return;
        var container = document.getElementById('pageArea');
        container.innerHTML = '';

        for (var p = 1; p <= currentBook.page_count; p++) {
            var wrap = document.createElement('div');
            wrap.className = 'pdf-page-container';
            wrap.id = 'pageWrap_' + p;

            var img = document.createElement('img');
            img.className = 'pdf-page';
            img.id = 'pageImg_' + p;
            img.alt = 'Page ' + p;
            img.src = '/page/' + currentBook.id + '/' + p + '?v=3';

            wrap.appendChild(img);
            container.appendChild(wrap);
        }

        updatePageDisplay(targetPage || 1);

        if (targetPage && targetPage > 1) {
            setTimeout(function() {
                var el = document.getElementById('pageWrap_' + targetPage);
                var container = document.getElementById('pageArea');
                if (el && container) {
                    container.scrollTop = el.offsetTop;
                }
            }, 60);
        } else {
            var container = document.getElementById('pageArea');
            if (container) container.scrollTop = 0;
        }
    }

    function updatePageDisplay(p) {
        if (!currentBook) return;
        document.getElementById('pageDisplay').innerText = p + ' / ' + currentBook.page_count;
        var progEl = document.getElementById('progress_' + currentBook.id);
        if (progEl) {
            progEl.innerText = 'Page ' + p + ' of ' + currentBook.page_count;
        }
        try {
            localStorage.setItem('page_' + currentBook.id, p);
        } catch(e) {}
    }

    var scrollTimer = null;
    function handleReaderScroll() {
        if (document.getElementById('readerView').style.display === 'none' || !currentBook) return;
        if (scrollTimer) return;
        scrollTimer = setTimeout(function() {
            scrollTimer = null;
            var wraps = document.getElementsByClassName('pdf-page-container');
            var container = document.getElementById('pageArea');
            var scrollY = container ? container.scrollTop : (window.pageYOffset || 0);
            var viewH = container ? container.clientHeight : (window.innerHeight || 768);
            var mid = scrollY + viewH * 0.4;
            for (var i = 0; i < wraps.length; i++) {
                var top = wraps[i].offsetTop;
                var bottom = top + wraps[i].offsetHeight;
                if (top <= mid && bottom >= mid) {
                    var p = i + 1;
                    updatePageDisplay(p);
                    var ping = new Image();
                    ping.src = '/bookmark/' + currentBook.id + '/' + p;
                    break;
                }
            }
        }, 120);
    }

    window.addEventListener('scroll', handleReaderScroll, false);
    var pAreaEl = document.getElementById('pageArea');
    if (pAreaEl) {
        pAreaEl.addEventListener('scroll', handleReaderScroll, false);
    }

    document.addEventListener('keydown', function(e) {
        if (document.getElementById('readerView').style.display === 'none') return;
        var pArea = document.getElementById('pageArea');
        if (!pArea) return;
        if (e.keyCode === 38 || e.keyCode === 37) pArea.scrollTop -= 200;
        else if (e.keyCode === 40 || e.keyCode === 39 || e.keyCode === 32) pArea.scrollTop += 200;
    }, false);

    var isCaching = false;
    function startOfflineCacheAll() {
        if (isCaching) return;
        isCaching = true;

        var statusTop = document.getElementById('cacheStatus');
        var statusLib = document.getElementById('libCacheStatus');
        statusTop.style.display = 'block';

        var queue = [];
        for (var i = 0; i < allBooks.length; i++) {
            var b = allBooks[i];
            for (var p = 1; p <= b.page_count; p++) {
                queue.push({ id: b.id, page: p, title: b.title, total: b.page_count });
            }
        }

        var totalItems = queue.length;
        var queueIdx = 0;

        function processQueue() {
            if (queueIdx >= totalItems) {
                var doneMsg = 'Saved all ' + allBooks.length + ' books (' + totalItems + ' pages) in grayscale. Ready offline.';
                statusTop.innerText = doneMsg;
                if (statusLib) statusLib.innerText = doneMsg;
                setTimeout(function() {
                    statusTop.style.display = 'none';
                }, 7000);
                isCaching = false;
                return;
            }

            var item = queue[queueIdx];
            var msg = 'Caching ' + item.title + ' (' + item.page + '/' + item.total + ') • ' + (queueIdx + 1) + '/' + totalItems;
            statusTop.innerText = msg;
            if (statusLib) statusLib.innerText = msg;

            var temp = new Image();
            temp.onload = temp.onerror = function() {
                temp.onload = null;
                temp.onerror = null;
                temp = null;
                queueIdx++;
                setTimeout(processQueue, 35);
            };
            temp.src = '/page/' + item.id + '/' + item.page + '?v=3';
        }

        processQueue();
    }

    window.addEventListener('load', function() {
        try {
            var savedBook = localStorage.getItem('ipad_active_book');
            if (savedBook && getBook(savedBook)) {
                openBook(savedBook);
            }
        } catch(e) {}
    }, false);

    document.addEventListener('contextmenu', function(e) {
        if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) return;
        e.preventDefault();
        return false;
    }, false);
    document.addEventListener('selectstart', function(e) {
        if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) return;
        e.preventDefault();
        return false;
    }, false);
    </script>

</body>
</html>'''

@app.route('/')
def index():
    conn = get_db()
    books_rows = conn.execute('SELECT * FROM books ORDER BY created_at DESC').fetchall()
    conn.close()

    books_data = [
        {
            'id': b['id'],
            'title': b['title'],
            'page_count': b['page_count'],
            'current_page': b['current_page']
        }
        for b in books_rows
    ]
    books_json = json.dumps(books_data)
    local_ip = get_local_ip()
    port = os.environ.get('PORT', 5000)

    return render_template_string(
        SPA_HTML,
        books=books_data,
        books_json=books_json,
        local_ip=local_ip,
        port=port
    )

@app.route('/read/<int:book_id>')
def read_book_redirect(book_id):
    return redirect(url_for('index'))

@app.route('/upload', methods=['POST'])
def upload():
    if 'pdf_file' not in request.files:
        return redirect(url_for('index'))
    file = request.files['pdf_file']
    if not file or not file.filename:
        return redirect(url_for('index'))
    
    orig_name = file.filename
    clean_title = Path(orig_name).stem.replace('_', ' ').replace('-', ' ').strip()
    timestamp = int(time.time())
    safe_filename = f"{timestamp}_{orig_name.replace(' ', '_')}"
    save_path = UPLOADS_DIR / safe_filename

    file.save(str(save_path))
    filesize = save_path.stat().st_size
    page_count = count_pages(save_path)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO books (title, filename, page_count, filesize, current_page)
        VALUES (?, ?, ?, ?, 1)
    ''', (clean_title, safe_filename, page_count, filesize))
    book_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # Pre-render page 1 & 2 in background so upload redirect is instant
    def prerender():
        render_page_to_jpeg(save_path, 1, CACHE_DIR / f"{book_id}_p1.jpg")
        if page_count > 1:
            render_page_to_jpeg(save_path, 2, CACHE_DIR / f"{book_id}_p2.jpg")
    threading.Thread(target=prerender, daemon=True).start()

    trigger_git_sync(f"Upload {clean_title}")

    return redirect(url_for('index'))

@app.route('/page/<int:book_id>/<int:page_num>')
def get_page(book_id, page_num):
    conn = get_db()
    book = conn.execute('SELECT * FROM books WHERE id = ?', (book_id,)).fetchone()
    conn.close()
    if not book:
        abort(404)

    cache_file = CACHE_DIR / f"{book_id}_p{page_num}.jpg"
    if not cache_file.exists():
        pdf_path = UPLOADS_DIR / book['filename']
        if not pdf_path.exists():
            abort(404)
        success = render_page_to_jpeg(pdf_path, page_num, cache_file)
        if not success:
            abort(500)

    # Return with permanent cache headers for offline iPad reading
    response = send_file(cache_file, mimetype='image/jpeg')
    response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return response

@app.route('/bookmark/<int:book_id>/<int:page_num>')
def bookmark(book_id, page_num):
    conn = get_db()
    with conn:
        conn.execute('UPDATE books SET current_page = ? WHERE id = ?', (page_num, book_id))
    conn.close()
    return ('', 204)

@app.route('/delete/<int:book_id>', methods=['POST'])
def delete_book(book_id):
    conn = get_db()
    book = conn.execute('SELECT * FROM books WHERE id = ?', (book_id,)).fetchone()
    book_title = book['title'] if book else ''
    if book:
        pdf_path = UPLOADS_DIR / book['filename']
        if pdf_path.exists():
            try:
                pdf_path.unlink()
            except Exception:
                pass
        
        for img in CACHE_DIR.glob(f"{book_id}_p*.jpg"):
            try:
                img.unlink()
            except Exception:
                pass

        with conn:
            conn.execute('DELETE FROM quizzes WHERE book_id = ?', (book_id,))
            conn.execute('DELETE FROM books WHERE id = ?', (book_id,))
    conn.close()

    if book_title:
        trigger_git_sync(f"Delete {book_title}")

    return redirect(url_for('index'))

QUIZ_HTML = '''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black">
    <title>Quiz - {{ book.title }}</title>
    <style>
    * {
        -webkit-box-sizing: border-box;
        box-sizing: border-box;
        border-radius: 0 !important;
        -webkit-touch-callout: none !important;
        -webkit-user-select: none !important;
        -khtml-user-select: none !important;
        -moz-user-select: none !important;
        -ms-user-select: none !important;
        user-select: none !important;
        -webkit-tap-highlight-color: rgba(0,0,0,0) !important;
    }
    input, textarea {
        -webkit-touch-callout: default !important;
        -webkit-user-select: text !important;
        user-select: text !important;
    }
    body, html {
        margin: 0;
        padding: 0;
        width: 100%;
        min-height: 100%;
        font-family: -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif;
        background-color: #ffffff;
        color: #000000;
        -webkit-overflow-scrolling: touch;
        overflow-x: hidden;
    }
    .container {
        padding: 14px;
        max-width: 800px;
        margin: 0 auto;
    }
    .header-box {
        background: #ffffff;
        border: 1px solid #000000;
        padding: 12px;
        margin-bottom: 12px;
        overflow: hidden;
    }
    .btn {
        display: inline-block;
        background: #000000;
        color: #ffffff !important;
        border: 1px solid #000000;
        padding: 8px 14px;
        font-size: 14px;
        font-weight: bold;
        cursor: pointer;
        text-align: center;
        text-decoration: none;
        -webkit-appearance: none;
        margin-right: 4px;
    }
    .btn-light {
        background: #ffffff;
        color: #000000 !important;
        border: 1px solid #000000;
    }
    .btn-active {
        background: #000000 !important;
        color: #ffffff !important;
        border: 1px solid #000000;
    }
    .count-bar {
        background: #f4f4f4;
        border: 1px solid #000000;
        padding: 8px 10px;
        margin-bottom: 14px;
        overflow: hidden;
        font-size: 13px;
        font-weight: bold;
        line-height: 28px;
    }
    .box {
        background: #ffffff;
        border: 1px solid #000000;
        padding: 14px;
        margin-bottom: 14px;
    }
    .box-title {
        font-size: 15px;
        font-weight: bold;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin: 0 0 10px 0;
        border-bottom: 1px solid #cccccc;
        padding-bottom: 6px;
    }
    .quiz-q-box {
        border: 1px solid #000000;
        padding: 12px;
        margin-bottom: 14px;
        background: #ffffff;
    }
    .quiz-q-title {
        font-weight: bold;
        font-size: 15px;
        line-height: 1.4;
        margin-bottom: 10px;
    }
    .quiz-opt {
        display: block;
        padding: 10px 12px;
        border: 1px solid #cccccc;
        margin-bottom: 8px;
        font-size: 14px;
        cursor: pointer;
        background: #ffffff;
        text-align: left;
        line-height: 1.35;
    }
    .quiz-opt.selected {
        border: 2px solid #000000;
        background: #f0f0f0;
        font-weight: bold;
    }
    .quiz-opt.correct {
        border: 2px solid #000000;
        background: #000000 !important;
        color: #ffffff !important;
        font-weight: bold;
    }
    .quiz-opt.incorrect {
        border: 1px solid #999999;
        color: #777777;
        text-decoration: line-through;
    }
    .explanation-box {
        display: none;
        margin-top: 8px;
        padding: 8px 10px;
        background: #f2f2f2;
        border-left: 3px solid #000000;
        font-size: 13px;
        line-height: 1.35;
    }
    .score-banner {
        display: none;
        border: 2px solid #000000;
        background: #f0f0f0;
        padding: 14px;
        margin-bottom: 14px;
        text-align: center;
        font-size: 16px;
        font-weight: bold;
    }
    </style>
</head>
<body>
<div class="container">
    <div class="header-box">
        <a href="/" class="btn btn-light" style="float: left;">&larr; Library</a>
        <a href="/quiz/{{ book.id }}?count={{ active_count }}&regenerate=1" class="btn" style="float: right;" onclick="this.innerHTML='Generating...';">&#8635; Regenerate</a>
        <div style="margin-left: 95px; margin-right: 125px; line-height: 32px; font-weight: bold; font-size: 15px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
            {{ book.title }}
        </div>
    </div>

    <!-- Question Count Selector -->
    <div class="count-bar">
        <span style="float: left; margin-right: 6px;">Questions:</span>
        <a href="/quiz/{{ book.id }}?count=5" class="btn {% if active_count == 5 %}btn-active{% else %}btn-light{% endif %}" style="padding: 3px 8px; font-size: 13px;">5 Qs</a>
        <a href="/quiz/{{ book.id }}?count=10" class="btn {% if active_count == 10 %}btn-active{% else %}btn-light{% endif %}" style="padding: 3px 8px; font-size: 13px;">10 Qs</a>
        <a href="/quiz/{{ book.id }}?count=15" class="btn {% if active_count == 15 %}btn-active{% else %}btn-light{% endif %}" style="padding: 3px 8px; font-size: 13px;">15 Qs</a>
        <a href="/quiz/{{ book.id }}?count=20" class="btn {% if active_count == 20 %}btn-active{% else %}btn-light{% endif %}" style="padding: 3px 8px; font-size: 13px;">20 Qs</a>
        <span style="float: right; color: #555555; font-size: 12px; font-weight: normal;">Full PDF Covered</span>
    </div>

    {% if quiz_items %}
    <div class="score-banner" id="scoreBanner"></div>

    <div id="quizList">
        {% for item in quiz_items %}
        {% set q_idx = loop.index0 %}
        <div class="quiz-q-box" id="qBox_{{ q_idx }}">
            <div class="quiz-q-title">{{ loop.index }}. {{ item.question }}</div>
            {% for opt in item.options %}
            {% set o_idx = loop.index0 %}
            <div class="quiz-opt" id="opt_{{ q_idx }}_{{ o_idx }}" onclick="selectOption({{ q_idx }}, {{ o_idx }})">
                <strong>{{ ['A', 'B', 'C', 'D'][o_idx] }}.</strong> {{ opt }}
            </div>
            {% endfor %}
            <div class="explanation-box" id="expl_{{ q_idx }}">
                <strong>Explanation:</strong> {{ item.explanation }}
            </div>
        </div>
        {% endfor %}
    </div>

    <div style="margin-top: 14px; text-align: center; padding-bottom: 30px;">
        <button onclick="checkAnswers()" id="checkBtn" class="btn" style="padding: 10px 24px; font-size: 15px;">Check Answers</button>
        <button onclick="resetQuiz()" id="resetBtn" class="btn btn-light" style="display: none; padding: 10px 24px; font-size: 15px; margin-left: 8px;">Try Again</button>
    </div>

    {% else %}
    <div class="box" style="text-align: center; padding: 30px;">
        <div class="box-title" style="border: none;">Quiz Generation Failed</div>
        <p style="color: #444444; margin-bottom: 20px;">Could not generate questions. Ensure the laptop server has internet access to reach the AI service.</p>
        <a href="/quiz/{{ book.id }}?count={{ active_count }}&regenerate=1" class="btn">&#8635; Retry Generation</a>
    </div>
    {% endif %}
</div>

<script>
var quizData = {{ quiz_json|safe }};
var userAnswers = {};
var isSubmitted = false;

function selectOption(qIdx, optIdx) {
    if (isSubmitted) return;
    userAnswers[qIdx] = optIdx;
    var item = quizData[qIdx];
    for (var i = 0; i < item.options.length; i++) {
        var el = document.getElementById('opt_' + qIdx + '_' + i);
        if (el) {
            if (i === optIdx) {
                el.className = 'quiz-opt selected';
            } else {
                el.className = 'quiz-opt';
            }
        }
    }
}

function checkAnswers() {
    if (!quizData || quizData.length === 0) return;
    isSubmitted = true;
    var correctCount = 0;

    for (var q = 0; q < quizData.length; q++) {
        var item = quizData[q];
        var chosen = userAnswers[q];
        var correct = item.answer;

        if (chosen === correct) {
            correctCount++;
        }

        for (var o = 0; o < item.options.length; o++) {
            var el = document.getElementById('opt_' + q + '_' + o);
            if (!el) continue;
            if (o === correct) {
                el.className = 'quiz-opt correct';
            } else if (o === chosen) {
                el.className = 'quiz-opt incorrect';
            } else {
                el.className = 'quiz-opt';
            }
        }

        var expl = document.getElementById('expl_' + q);
        if (expl) expl.style.display = 'block';
    }

    var banner = document.getElementById('scoreBanner');
    var pct = Math.round((correctCount / quizData.length) * 100);
    banner.innerText = 'SCORE: ' + correctCount + ' / ' + quizData.length + ' (' + pct + '%)';
    banner.style.display = 'block';

    document.getElementById('checkBtn').style.display = 'none';
    document.getElementById('resetBtn').style.display = 'inline-block';
    window.scrollTo(0, 0);
}

function resetQuiz() {
    isSubmitted = false;
    userAnswers = {};
    for (var q = 0; q < quizData.length; q++) {
        var item = quizData[q];
        for (var o = 0; o < item.options.length; o++) {
            var el = document.getElementById('opt_' + q + '_' + o);
            if (el) el.className = 'quiz-opt';
        }
        var expl = document.getElementById('expl_' + q);
        if (expl) expl.style.display = 'none';
    }
    document.getElementById('scoreBanner').style.display = 'none';
    document.getElementById('checkBtn').style.display = 'inline-block';
    document.getElementById('resetBtn').style.display = 'none';
}

document.addEventListener('contextmenu', function(e) {
    if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) return;
    e.preventDefault();
    return false;
}, false);
document.addEventListener('selectstart', function(e) {
    if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) return;
    e.preventDefault();
    return false;
}, false);
</script>
</body>
</html>'''

@app.route('/quiz/<int:book_id>')
def quiz_view(book_id):
    conn = get_db()
    book = conn.execute('SELECT * FROM books WHERE id = ?', (book_id,)).fetchone()
    if not book:
        conn.close()
        abort(404)

    try:
        count = int(request.args.get('count', 5))
        if count not in [5, 10, 15, 20]:
            count = 5
    except Exception:
        count = 5

    regenerate = request.args.get('regenerate') == '1'
    quiz_row = conn.execute(
        'SELECT * FROM quizzes WHERE book_id = ? AND q_count = ?',
        (book_id, count)
    ).fetchone()
    quiz_items = None

    if quiz_row and not regenerate:
        try:
            quiz_items = json.loads(quiz_row['quiz_data'])
        except Exception:
            quiz_items = None

    if not quiz_items:
        pdf_path = UPLOADS_DIR / book['filename']
        if pdf_path.exists():
            quiz_items = generate_quiz_for_pdf(pdf_path, book['title'], count=count)
            if quiz_items:
                quiz_json = json.dumps(quiz_items)
                with conn:
                    conn.execute(
                        'INSERT OR REPLACE INTO quizzes (book_id, q_count, quiz_data) VALUES (?, ?, ?)',
                        (book_id, count, quiz_json)
                    )

    conn.close()

    return render_template_string(
        QUIZ_HTML,
        book=book,
        active_count=count,
        quiz_items=quiz_items,
        quiz_json=json.dumps(quiz_items if quiz_items else [])
    )

@app.route('/chat/<int:book_id>', methods=['POST'])
def chat_book(book_id):
    conn = get_db()
    book = conn.execute('SELECT * FROM books WHERE id = ?', (book_id,)).fetchone()
    conn.close()

    if not book:
        return jsonify({'error': 'Book not found'}), 404

    data = request.get_json(silent=True) or {}
    question = (data.get('question') or '').strip()
    if not question:
        return jsonify({'error': 'Question cannot be empty'}), 400

    pdf_path = UPLOADS_DIR / book['filename']
    if not pdf_path.exists():
        return jsonify({'error': 'PDF file not found on server'}), 404

    doc_text = extract_pdf_text(pdf_path)
    if not doc_text:
        return jsonify({'error': 'Could not extract text from document'}), 500

    # Limit text context to avoid exceeding token limit or latency
    context_text = doc_text[:120000]

    prompt = (
        f"You are a helpful study assistant for the document titled '{book['title']}'.\n"
        f"Below is the full text of the document:\n"
        f"---\n{context_text}\n---\n\n"
        f"User question: {question}\n\n"
        f"Instructions:\n"
        f"- Answer the question accurately based directly on the provided document.\n"
        f"- Keep your answer concise, direct, and easy to read on a mobile screen.\n"
        f"- Do NOT use markdown symbols like asterisks, hashtags, or markdown bolding. Use clean plain text."
    )

    answer = call_gemini(prompt, timeout=25)
    if not answer:
        return jsonify({'error': 'AI failed to generate a response. Please check connection or try again.'}), 502

    # Clean any accidental markdown stars/hashes
    clean_answer = answer.replace('*', '').replace('#', '').strip()
    return jsonify({'answer': clean_answer})

@app.route('/offline.appcache')
def offline_manifest():
    conn = get_db()
    books = conn.execute('SELECT id, page_count FROM books').fetchall()
    conn.close()

    version = int(time.time() // 60)
    lines = [
        'CACHE MANIFEST',
        f'# Version {version}',
        'CACHE:',
        '/',
        '/icon.png'
    ]
    for b in books:
        for p in range(1, b["page_count"] + 1):
            lines.append(f'/page/{b["id"]}/{p}?v=3')
    lines.append('NETWORK:')
    lines.append('*')

    response = app.response_class('\n'.join(lines), mimetype='text/cache-manifest')
    response.headers['Cache-Control'] = 'no-cache'
    return response

@app.route('/icon.png')
@app.route('/apple-touch-icon.png')
def app_icon():
    icon_path = BASE_DIR / 'static' / 'icon.png'
    if icon_path.exists():
        return send_file(icon_path, mimetype='image/png')
    abort(404)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"iPadPDF Server running on port {port}")
    print(f"Access on iPad at: http://{get_local_ip()}:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
