#!/usr/bin/env python3
"""
iPadPDF - Ultra-Lightweight Single Page Application (SPA) for iPad 1st Gen (iOS 5)
100% Offline-Resilient:
- All books and reader in ONE single HTML page (no broken offline page navigations)
- URL never changes, so Safari Refresh NEVER breaks
- Automatically restores last book and page from localStorage on reload
- Sequential memory-safe cache for all books
- Pure swipe & edge-tap navigation
"""

import os
import sys
import time
import socket
import sqlite3
import subprocess
import shutil
import json
from pathlib import Path
from flask import Flask, request, redirect, url_for, send_file, render_template_string, abort
import pypdfium2 as pdfium

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / 'uploads'
CACHE_DIR = BASE_DIR / 'cache'
DATA_DIR = BASE_DIR / 'data'
DB_PATH = DATA_DIR / 'library.db'

for d in (UPLOADS_DIR, CACHE_DIR, DATA_DIR):
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 250 * 1024 * 1024  # 250 MB max PDF upload

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

def render_page_to_jpeg(pdf_path, page_num, output_jpg, dpi=130, quality=78):
    output_jpg = Path(output_jpg)
    if output_jpg.exists():
        return True

    # Method 1: Try native pdftoppm (fastest C++ implementation)
    if shutil.which('pdftoppm'):
        prefix = output_jpg.with_suffix('')
        cmd = [
            'pdftoppm',
            '-jpeg',
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

    # Method 2: Fallback to portable pypdfium2
    try:
        doc = pdfium.PdfDocument(str(pdf_path))
        if page_num < 1 or page_num > len(doc):
            doc.close()
            return False
        page = doc[page_num - 1]
        scale = dpi / 72.0
        pil_img = page.render(scale=scale).to_pil()
        pil_img.save(str(output_jpg), 'JPEG', quality=quality)
        doc.close()
        return True
    except Exception as e:
        app.logger.error(f'Failed rendering page {page_num}: {e}')
        return False

# Single unified HTML page containing both Library and Fullscreen Reader
SPA_HTML = '''<!DOCTYPE html>
<html manifest="/offline.appcache">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=3.0, user-scalable=yes">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <link rel="apple-touch-icon" href="/icon.png">
    <title>iPad PDF</title>
    <style>
    * {
        -webkit-box-sizing: border-box;
        box-sizing: border-box;
    }
    body, html {
        margin: 0;
        padding: 0;
        width: 100%;
        min-height: 100%;
        font-family: -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif;
    }
    
    /* Library View Styles */
    #libraryView {
        background-color: #f7f7f7;
        color: #222222;
        padding: 12px;
        font-size: 16px;
        line-height: 1.4;
    }
    .header {
        background: #ffffff;
        border: 1px solid #d0d0d0;
        padding: 14px 16px;
        margin-bottom: 14px;
        border-radius: 4px;
    }
    .header h1 {
        margin: 0 0 6px 0;
        font-size: 22px;
    }
    .ip-banner {
        background: #eef7ff;
        border: 1px solid #b8daff;
        padding: 10px 14px;
        margin-bottom: 14px;
        border-radius: 4px;
        font-size: 15px;
    }
    .card {
        background: #ffffff;
        border: 1px solid #d0d0d0;
        padding: 14px;
        margin-bottom: 14px;
        border-radius: 4px;
    }
    .btn {
        display: inline-block;
        background: #007aff;
        color: #ffffff !important;
        padding: 8px 16px;
        border: 1px solid #0056b3;
        border-radius: 4px;
        font-size: 15px;
        font-weight: bold;
        cursor: pointer;
        text-align: center;
        text-decoration: none;
        -webkit-appearance: none;
    }
    .btn-success {
        background: #28a745;
        border-color: #1e7e34;
    }
    .btn-danger {
        background: #dc3545;
        border-color: #bd2130;
        padding: 6px 12px;
        font-size: 13px;
    }
    .book-table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 10px;
    }
    .book-table th, .book-table td {
        padding: 12px 8px;
        border-bottom: 1px solid #e0e0e0;
        text-align: left;
        vertical-align: middle;
    }
    .book-table th {
        background: #f0f0f0;
        font-size: 14px;
    }
    input[type="file"] {
        font-size: 16px;
        padding: 8px;
        margin-bottom: 12px;
        display: block;
    }

    /* Reader View Styles (Dark Fullscreen) */
    #readerView {
        display: none;
        background-color: #1e1e1e;
        color: #ffffff;
        min-height: 100%;
        width: 100%;
        overflow-x: hidden;
    }
    #topBar {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        height: 38px;
        background: rgba(0, 0, 0, 0.90);
        border-bottom: 1px solid #333333;
        padding: 5px 12px;
        z-index: 999;
        font-size: 15px;
    }
    .nav-btn {
        color: #5ac8fa !important;
        text-decoration: none;
        font-weight: bold;
        float: left;
        line-height: 28px;
        font-size: 15px;
        margin-right: 10px;
        cursor: pointer;
    }
    .page-badge {
        float: right;
        color: #cccccc;
        font-size: 14px;
        line-height: 28px;
        font-weight: bold;
    }
    #pageArea {
        padding-top: 40px;
        text-align: center;
        width: 100%;
        min-height: 100%;
    }
    #pageImg {
        display: block;
        margin: 0 auto;
        max-width: 100%;
        height: auto;
        background: #ffffff;
        box-shadow: 0 4px 14px rgba(0,0,0,0.5);
    }
    #tapHint {
        font-size: 12px;
        color: #777777;
        text-align: center;
        padding: 8px 0;
    }
    #cacheStatus {
        display: none;
        background: #2a2a2a;
        border-bottom: 1px solid #444;
        color: #ffcc00;
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

    <!-- ==================== 1. LIBRARY VIEW ==================== -->
    <div id="libraryView">
        <div class="header">
            <h1>iPad PDF Library</h1>
            <div>Offline PDF Reader for Gen 1 iPad. Swipe left/right to turn pages.</div>
        </div>

        <div class="ip-banner">
            <strong>Local Address:</strong> Open Safari on your iPad and go to:<br>
            <span style="font-size: 20px; font-weight: bold; color: #0056b3;">http://{{ local_ip }}:{{ port }}</span>
        </div>

        <div class="card" style="background: #fff8e1; border-color: #ffe082;">
            <strong style="color: #b78103; font-size: 16px;">School / Offline Preparation:</strong><br>
            <span style="font-size: 14px; color: #555;">Tap this button once while connected to Wi-Fi to save all reviewers to your iPad storage:</span><br><br>
            <button onclick="startOfflineCacheAll()" id="libCacheBtn" class="btn btn-success" style="font-size: 16px; padding: 10px 20px;">Save All Books for Offline (School)</button>
            <div id="libCacheStatus" style="margin-top: 8px; font-weight: bold; color: #b78103;"></div>
        </div>

        <div class="card">
            <h2 style="margin-top: 0; font-size: 18px;">Upload New PDF</h2>
            <form action="/upload" method="post" enctype="multipart/form-data">
                <input type="file" name="pdf_file" accept=".pdf,application/pdf" required>
                <input type="submit" value="Upload PDF" class="btn">
            </form>
        </div>

        <div class="card">
            <h2 style="margin-top: 0; font-size: 18px;">My Documents ({{ books|length }})</h2>
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
                            <strong><a href="javascript:void(0)" onclick="openBook({{ b.id }})" style="color: #0066cc; font-size: 16px;">{{ b.title }}</a></strong>
                            <div style="font-size: 13px; color: #666;" id="progress_{{ b.id }}">Page {{ b.current_page }} of {{ b.page_count }}</div>
                        </td>
                        <td>{{ b.page_count }}</td>
                        <td>
                            <button onclick="openBook({{ b.id }})" class="btn">Open</button>
                            <form action="/delete/{{ b.id }}" method="post" style="display: inline; margin-left: 6px;" onsubmit="return confirm('Delete this PDF?');">
                                <input type="submit" value="Delete" class="btn btn-danger">
                            </form>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <p style="color: #666;">No PDFs uploaded yet. Upload one above from any phone or laptop!</p>
            {% endif %}
        </div>
    </div>


    <!-- ==================== 2. FULLSCREEN READER VIEW ==================== -->
    <div id="readerView">
        <div id="topBar">
            <a href="javascript:void(0)" onclick="closeBook()" class="nav-btn">&larr; Library</a>
            <select id="readerBookSelect" onchange="openBook(this.value)" style="background: #2b2b2b; color: #ffffff; border: 1px solid #555555; padding: 4px 6px; font-size: 14px; border-radius: 4px; max-width: 170px; float: left; margin-top: 2px; -webkit-appearance: menulist;">
                {% for b in books %}
                <option value="{{ b.id }}">{{ b.title }} ({{ b.page_count }}p)</option>
                {% endfor %}
            </select>
            <a href="javascript:void(0)" onclick="startOfflineCacheAll()" class="nav-btn" id="readerCacheBtn" style="margin-left: 10px; font-weight: normal; color: #ffcc00 !important; font-size: 13px;">Save All</a>
            <span class="page-badge" id="pageDisplay">1 / 1</span>
        </div>
        <div id="cacheStatus"></div>

        <div id="pageArea">
            <img id="pageImg" src="" alt="PDF Page">
            <div id="tapHint">Swipe left for Next &bull; Swipe right for Prev</div>
        </div>
    </div>


    <!-- ==================== 3. CLIENT SCRIPT (ES5 for iOS 5 Safari) ==================== -->
    <script>
    var allBooks = {{ books_json|safe }};
    var currentBook = null;
    var currentPage = 1;

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

        if (page) {
            currentPage = page;
        } else {
            // Check localStorage or default to 1
            try {
                var saved = localStorage.getItem('page_' + b.id);
                currentPage = saved ? parseInt(saved, 10) : (b.current_page || 1);
            } catch(e) {
                currentPage = b.current_page || 1;
            }
        }
        currentPage = Math.max(1, Math.min(currentPage, b.page_count));

        // Save active book & page to localStorage
        try {
            localStorage.setItem('ipad_active_book', b.id);
            localStorage.setItem('page_' + b.id, currentPage);
        } catch(e) {}

        // Switch to reader view without changing browser URL
        document.getElementById('libraryView').style.display = 'none';
        document.getElementById('readerView').style.display = 'block';
        document.getElementById('readerBookSelect').value = b.id;

        renderPage();
    }

    function closeBook() {
        try {
            localStorage.removeItem('ipad_active_book');
        } catch(e) {}
        document.getElementById('readerView').style.display = 'none';
        document.getElementById('libraryView').style.display = 'block';
        window.scrollTo(0, 0);
    }

    function renderPage() {
        if (!currentBook) return;
        var img = document.getElementById('pageImg');
        img.src = '/page/' + currentBook.id + '/' + currentPage;

        document.getElementById('pageDisplay').innerText = currentPage + ' / ' + currentBook.page_count;
        window.scrollTo(0, 0);

        // Update progress in localStorage
        try {
            localStorage.setItem('page_' + currentBook.id, currentPage);
        } catch(e) {}

        // Update label in library view if present
        var progEl = document.getElementById('progress_' + currentBook.id);
        if (progEl) {
            progEl.innerText = 'Page ' + currentPage + ' of ' + currentBook.page_count;
        }

        // Silent bookmark ping if network available
        var ping = new Image();
        ping.src = '/bookmark/' + currentBook.id + '/' + currentPage;

        // Preload next and previous
        if (currentPage < currentBook.page_count) {
            var n = new Image();
            n.src = '/page/' + currentBook.id + '/' + (currentPage + 1);
        }
        if (currentPage > 1) {
            var p = new Image();
            p.src = '/page/' + currentBook.id + '/' + (currentPage - 1);
        }
    }

    function nextPage() {
        if (currentBook && currentPage < currentBook.page_count) {
            currentPage++;
            renderPage();
        }
    }

    function prevPage() {
        if (currentBook && currentPage > 1) {
            currentPage--;
            renderPage();
        }
    }

    // Touch swipe navigation
    var startX = 0, startY = 0, startTime = 0;
    window.addEventListener('touchstart', function(e) {
        if (e.touches && e.touches.length === 1) {
            startX = e.touches[0].clientX;
            startY = e.touches[0].clientY;
            startTime = new Date().getTime();
        }
    }, false);

    window.addEventListener('touchend', function(e) {
        if (document.getElementById('readerView').style.display === 'none') return;
        if (!e.changedTouches || e.changedTouches.length !== 1) return;

        var endX = e.changedTouches[0].clientX;
        var endY = e.changedTouches[0].clientY;
        var diffX = endX - startX;
        var diffY = endY - startY;
        var absX = Math.abs(diffX);
        var absY = Math.abs(diffY);
        var duration = new Date().getTime() - startTime;

        // Horizontal swipe gesture
        if (absX >= 35 && absX > absY && duration < 900) {
            if (diffX < 0) nextPage();
            else prevPage();
            return;
        }

        // Light edge tap (right 30% = next, left 30% = prev)
        if (absX < 15 && absY < 15 && duration < 350) {
            var width = window.innerWidth || 1024;
            if (endX > width * 0.70) nextPage();
            else if (endX < width * 0.30) prevPage();
        }
    }, false);

    // Keyboard support
    document.addEventListener('keydown', function(e) {
        if (document.getElementById('readerView').style.display === 'none') return;
        if (e.keyCode === 37) prevPage();
        else if (e.keyCode === 39 || e.keyCode === 32) nextPage();
    }, false);

    // Batch offline caching (for all books)
    var isCaching = false;
    function startOfflineCacheAll() {
        if (isCaching) return;
        isCaching = true;

        var statusTop = document.getElementById('cacheStatus');
        var statusLib = document.getElementById('libCacheStatus');
        statusTop.style.display = 'block';
        statusTop.style.color = '#ffcc00';

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
                var doneMsg = 'Saved all ' + allBooks.length + ' books (' + totalItems + ' pages)! Ready for school offline.';
                statusTop.style.color = '#34c759';
                statusTop.innerText = doneMsg;
                if (statusLib) {
                    statusLib.style.color = '#28a745';
                    statusLib.innerText = doneMsg;
                }
                setTimeout(function() {
                    statusTop.style.display = 'none';
                }, 7000);
                isCaching = false;
                return;
            }

            var item = queue[queueIdx];
            var msg = 'Saving ' + item.title + ' (' + item.page + '/' + item.total + ') • ' + (queueIdx + 1) + '/' + totalItems;
            statusTop.innerText = msg;
            if (statusLib) statusLib.innerText = msg;

            var temp = new Image();
            temp.onload = temp.onerror = function() {
                temp.onload = null;
                temp.onerror = null;
                temp = null;
                queueIdx++;
                setTimeout(processQueue, 40);
            };
            temp.src = '/page/' + item.id + '/' + item.page;
        }

        processQueue();
    }

    // Auto-restore last opened book on reload / refresh
    window.addEventListener('load', function() {
        try {
            var savedBook = localStorage.getItem('ipad_active_book');
            if (savedBook && getBook(savedBook)) {
                openBook(savedBook);
            }
        } catch(e) {}
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
    # Redirect legacy /read/X links straight to root SPA
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

    # Pre-render page 1 and page 2 so they are ready instantly
    render_page_to_jpeg(save_path, 1, CACHE_DIR / f"{book_id}_p1.jpg")
    if page_count > 1:
        render_page_to_jpeg(save_path, 2, CACHE_DIR / f"{book_id}_p2.jpg")

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
    if book:
        pdf_path = UPLOADS_DIR / book['filename']
        if pdf_path.exists():
            try:
                pdf_path.unlink()
            except Exception:
                pass
        
        # Clean up cached images for this book
        for img in CACHE_DIR.glob(f"{book_id}_p*.jpg"):
            try:
                img.unlink()
            except Exception:
                pass

        with conn:
            conn.execute('DELETE FROM books WHERE id = ?', (book_id,))
    conn.close()
    return redirect(url_for('index'))

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
            lines.append(f'/page/{b["id"]}/{p}')
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
