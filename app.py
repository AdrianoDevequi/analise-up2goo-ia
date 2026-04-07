import os
import threading
import string
import random
from datetime import datetime
from contextlib import contextmanager
from functools import wraps

import psycopg2
import psycopg2.extras
from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify, g
)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

from crawler import crawl_website
from analyzer import analyze_page, get_ai_suggestions

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(32).hex())


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db_config():
    return {
        'host': os.environ.get('DB_HOST', 'localhost'),
        'port': int(os.environ.get('DB_PORT', 5432)),
        'dbname': os.environ.get('DB_NAME', 'analise_ia'),
        'user': os.environ.get('DB_USER', 'analise_user'),
        'password': os.environ.get('DB_PASSWORD', 'analise_pass123'),
    }


def get_db():
    """Returns a database connection attached to Flask's g context."""
    if '_db' not in g:
        g._db = psycopg2.connect(
            **get_db_config(),
            cursor_factory=psycopg2.extras.RealDictCursor
        )
    return g._db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop('_db', None)
    if db is not None:
        db.close()


@contextmanager
def get_thread_db():
    """Database connection for background threads (no Flask context)."""
    conn = psycopg2.connect(
        **get_db_config(),
        cursor_factory=psycopg2.extras.RealDictCursor
    )
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Create tables and default admin user."""
    with get_thread_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'client',
                    created_at TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS projects (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    client_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    plain_password TEXT,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS analyses (
                    id SERIAL PRIMARY KEY,
                    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    status TEXT DEFAULT 'pending',
                    total_pages INTEGER DEFAULT 0,
                    total_issues INTEGER DEFAULT 0,
                    high_issues INTEGER DEFAULT 0,
                    medium_issues INTEGER DEFAULT 0,
                    low_issues INTEGER DEFAULT 0,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS pages (
                    id SERIAL PRIMARY KEY,
                    analysis_id INTEGER NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
                    url TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    status_code INTEGER DEFAULT 0,
                    word_count INTEGER DEFAULT 0,
                    load_time REAL DEFAULT 0,
                    issue_count INTEGER DEFAULT 0,
                    analyzed_at TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS issues (
                    id SERIAL PRIMARY KEY,
                    page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
                    category TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    current_value TEXT DEFAULT '',
                    suggestion TEXT DEFAULT '',
                    ai_suggestion JSONB
                );
            ''')
            conn.commit()

            # Default admin
            admin_email = os.environ.get('ADMIN_EMAIL', 'admin@seudominio.com')
            admin_password = os.environ.get('ADMIN_PASSWORD', 'admin123')
            admin_name = os.environ.get('ADMIN_NAME', 'Administrador')

            cur.execute('SELECT id FROM users WHERE role = %s LIMIT 1', ('admin',))
            if not cur.fetchone():
                cur.execute(
                    'INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, %s)',
                    (admin_name, admin_email, generate_password_hash(admin_password), 'admin')
                )
                conn.commit()
                print(f'[INIT] Admin criado: {admin_email} / {admin_password}')


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def generate_password(length=10):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('Acesso restrito a administradores.', 'danger')
            return redirect(url_for('client_dashboard'))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Background analysis
# ---------------------------------------------------------------------------

def run_analysis_background(analysis_id, project_url):
    """Runs the full crawl + SEO analysis in a background thread."""
    with get_thread_db() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    'UPDATE analyses SET status=%s, started_at=%s WHERE id=%s',
                    ('running', datetime.now(), analysis_id)
                )
                conn.commit()

                pages = crawl_website(project_url, max_pages=30)

                total_issues = 0
                high_issues = 0
                medium_issues = 0
                low_issues = 0

                has_api_key = bool(os.environ.get('ANTHROPIC_API_KEY', '').strip())

                for page_data in pages:
                    issues, word_count = analyze_page(page_data)

                    # AI suggestions (only first 10 pages to control API usage)
                    ai_data = None
                    if has_api_key and len(pages) <= 10:
                        ai_data = get_ai_suggestions(page_data)

                    cur.execute(
                        '''INSERT INTO pages (analysis_id, url, title, status_code, word_count, load_time, issue_count)
                           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id''',
                        (
                            analysis_id,
                            page_data['url'],
                            page_data.get('title', ''),
                            page_data.get('status_code', 0),
                            word_count,
                            page_data.get('load_time', 0),
                            len(issues)
                        )
                    )
                    page_id = cur.fetchone()['id']
                    conn.commit()

                    for issue in issues:
                        import json
                        ai_suggestion_json = None
                        if ai_data:
                            ai_suggestion_json = json.dumps(ai_data, ensure_ascii=False)

                        cur.execute(
                            '''INSERT INTO issues (page_id, category, severity, title, description, current_value, suggestion, ai_suggestion)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''',
                            (
                                page_id,
                                issue['category'],
                                issue['severity'],
                                issue['title'],
                                issue.get('description', ''),
                                issue.get('current_value', ''),
                                issue.get('suggestion', ''),
                                ai_suggestion_json
                            )
                        )
                        total_issues += 1
                        if issue['severity'] == 'high':
                            high_issues += 1
                        elif issue['severity'] == 'medium':
                            medium_issues += 1
                        else:
                            low_issues += 1

                    conn.commit()

                cur.execute(
                    '''UPDATE analyses SET
                        status=%s, completed_at=%s,
                        total_pages=%s, total_issues=%s,
                        high_issues=%s, medium_issues=%s, low_issues=%s
                       WHERE id=%s''',
                    (
                        'completed', datetime.now(),
                        len(pages), total_issues,
                        high_issues, medium_issues, low_issues,
                        analysis_id
                    )
                )
                conn.commit()

            except Exception as e:
                print(f'[ANALYSIS ERROR] {e}')
                try:
                    cur.execute(
                        "UPDATE analyses SET status='error', error_message=%s WHERE id=%s",
                        (str(e), analysis_id)
                    )
                    conn.commit()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    if 'user_id' in session:
        if session.get('role') == 'admin':
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('client_dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        db = get_db()
        with db.cursor() as cur:
            cur.execute('SELECT * FROM users WHERE email = %s', (email,))
            user = cur.fetchone()

        if user and check_password_hash(user['password_hash'], password):
            session.clear()
            session['user_id'] = user['id']
            session['role'] = user['role']
            session['name'] = user['name']
            if user['role'] == 'admin':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('client_dashboard'))

        flash('E-mail ou senha incorretos.', 'danger')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@app.route('/admin')
@admin_required
def admin_dashboard():
    db = get_db()
    with db.cursor() as cur:
        cur.execute('SELECT COUNT(*) as total FROM projects')
        total_projects = cur.fetchone()['total']

        cur.execute("SELECT COUNT(*) as total FROM users WHERE role='client'")
        total_clients = cur.fetchone()['total']

        cur.execute("SELECT COUNT(*) as total FROM analyses WHERE status='completed'")
        total_analyses = cur.fetchone()['total']

        cur.execute('''
            SELECT p.id, p.name, p.url, p.created_at,
                   u.name as client_name, u.email as client_email,
                   (SELECT status FROM analyses WHERE project_id=p.id ORDER BY created_at DESC LIMIT 1) as last_status,
                   (SELECT completed_at FROM analyses WHERE project_id=p.id ORDER BY created_at DESC LIMIT 1) as last_analysis
            FROM projects p
            JOIN users u ON u.id = p.client_id
            ORDER BY p.created_at DESC
            LIMIT 10
        ''')
        recent_projects = cur.fetchall()

    return render_template('admin/dashboard.html',
                           total_projects=total_projects,
                           total_clients=total_clients,
                           total_analyses=total_analyses,
                           recent_projects=recent_projects)


@app.route('/admin/projetos')
@admin_required
def admin_projects():
    db = get_db()
    with db.cursor() as cur:
        cur.execute('''
            SELECT p.id, p.name, p.url, p.created_at,
                   u.name as client_name, u.email as client_email,
                   (SELECT status FROM analyses WHERE project_id=p.id ORDER BY created_at DESC LIMIT 1) as last_status,
                   (SELECT total_issues FROM analyses WHERE project_id=p.id AND status='completed' ORDER BY completed_at DESC LIMIT 1) as last_issues,
                   (SELECT completed_at FROM analyses WHERE project_id=p.id AND status='completed' ORDER BY completed_at DESC LIMIT 1) as last_analysis
            FROM projects p
            JOIN users u ON u.id = p.client_id
            ORDER BY p.created_at DESC
        ''')
        projects = cur.fetchall()

    return render_template('admin/projects.html', projects=projects)


@app.route('/admin/projetos/novo', methods=['GET', 'POST'])
@admin_required
def admin_new_project():
    if request.method == 'POST':
        project_name = request.form.get('project_name', '').strip()
        client_name = request.form.get('client_name', '').strip()
        client_email = request.form.get('client_email', '').strip().lower()
        project_url = request.form.get('project_url', '').strip()
        notes = request.form.get('notes', '').strip()

        if not all([project_name, client_name, client_email, project_url]):
            flash('Preencha todos os campos obrigatórios.', 'danger')
            return render_template('admin/new_project.html')

        # Normalize URL
        if not project_url.startswith(('http://', 'https://')):
            project_url = 'https://' + project_url

        db = get_db()
        with db.cursor() as cur:
            # Check if client user exists
            cur.execute('SELECT id FROM users WHERE email = %s', (client_email,))
            existing_user = cur.fetchone()

            plain_password = generate_password(10)

            if existing_user:
                client_id = existing_user['id']
                # Update password so admin has the current one
                cur.execute(
                    'UPDATE users SET password_hash=%s, name=%s WHERE id=%s',
                    (generate_password_hash(plain_password), client_name, client_id)
                )
            else:
                cur.execute(
                    'INSERT INTO users (name, email, password_hash, role) VALUES (%s, %s, %s, %s) RETURNING id',
                    (client_name, client_email, generate_password_hash(plain_password), 'client')
                )
                client_id = cur.fetchone()['id']

            cur.execute(
                'INSERT INTO projects (name, url, client_id, plain_password, notes) VALUES (%s, %s, %s, %s, %s) RETURNING id',
                (project_name, project_url, client_id, plain_password, notes)
            )
            project_id = cur.fetchone()['id']
            db.commit()

        flash(f'Projeto criado! Credenciais do cliente: {client_email} / {plain_password}', 'success')
        return redirect(url_for('admin_project_detail', project_id=project_id))

    return render_template('admin/new_project.html')


@app.route('/admin/projetos/<int:project_id>')
@admin_required
def admin_project_detail(project_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute('''
            SELECT p.*, u.name as client_name, u.email as client_email
            FROM projects p
            JOIN users u ON u.id = p.client_id
            WHERE p.id = %s
        ''', (project_id,))
        project = cur.fetchone()

        if not project:
            flash('Projeto não encontrado.', 'danger')
            return redirect(url_for('admin_projects'))

        cur.execute('''
            SELECT * FROM analyses WHERE project_id = %s ORDER BY created_at DESC
        ''', (project_id,))
        analyses = cur.fetchall()

        latest_analysis = analyses[0] if analyses else None
        pages_data = []

        if latest_analysis and latest_analysis['status'] == 'completed':
            cur.execute('''
                SELECT pg.*,
                       COUNT(i.id) as issue_count,
                       COUNT(CASE WHEN i.severity='high' THEN 1 END) as high_count,
                       COUNT(CASE WHEN i.severity='medium' THEN 1 END) as medium_count,
                       COUNT(CASE WHEN i.severity='low' THEN 1 END) as low_count
                FROM pages pg
                LEFT JOIN issues i ON i.page_id = pg.id
                WHERE pg.analysis_id = %s
                GROUP BY pg.id
                ORDER BY issue_count DESC
            ''', (latest_analysis['id'],))
            pages_data = cur.fetchall()

    return render_template('admin/project_detail.html',
                           project=project,
                           analyses=analyses,
                           latest_analysis=latest_analysis,
                           pages_data=pages_data)


@app.route('/admin/projetos/<int:project_id>/analisar', methods=['POST'])
@admin_required
def admin_run_analysis(project_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute('SELECT * FROM projects WHERE id = %s', (project_id,))
        project = cur.fetchone()

        if not project:
            return jsonify({'error': 'Projeto não encontrado'}), 404

        # Check if there's already a running analysis
        cur.execute(
            "SELECT id FROM analyses WHERE project_id=%s AND status IN ('pending','running')",
            (project_id,)
        )
        running = cur.fetchone()
        if running:
            return jsonify({'error': 'Já existe uma análise em andamento para este projeto'}), 400

        cur.execute(
            'INSERT INTO analyses (project_id, status) VALUES (%s, %s) RETURNING id',
            (project_id, 'pending')
        )
        analysis_id = cur.fetchone()['id']
        db.commit()

    # Start background thread
    thread = threading.Thread(
        target=run_analysis_background,
        args=(analysis_id, project['url']),
        daemon=True
    )
    thread.start()

    return jsonify({'analysis_id': analysis_id, 'status': 'started'})


@app.route('/admin/projetos/<int:project_id>/excluir', methods=['POST'])
@admin_required
def admin_delete_project(project_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute('DELETE FROM projects WHERE id = %s', (project_id,))
        db.commit()
    flash('Projeto excluído com sucesso.', 'success')
    return redirect(url_for('admin_projects'))


@app.route('/admin/analise/<int:analysis_id>/pagina/<int:page_id>')
@admin_required
def admin_page_issues(analysis_id, page_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute('SELECT * FROM pages WHERE id = %s AND analysis_id = %s', (page_id, analysis_id))
        page = cur.fetchone()
        if not page:
            return jsonify({'error': 'Página não encontrada'}), 404

        cur.execute(
            'SELECT * FROM issues WHERE page_id = %s ORDER BY CASE severity WHEN %s THEN 1 WHEN %s THEN 2 ELSE 3 END',
            (page_id, 'high', 'medium')
        )
        issues = cur.fetchall()

    return jsonify({
        'page': dict(page),
        'issues': [dict(i) for i in issues]
    })


# ---------------------------------------------------------------------------
# API - Analysis status (polling)
# ---------------------------------------------------------------------------

@app.route('/api/analysis/<int:analysis_id>/status')
@login_required
def api_analysis_status(analysis_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute('SELECT * FROM analyses WHERE id = %s', (analysis_id,))
        analysis = cur.fetchone()
        if not analysis:
            return jsonify({'error': 'Não encontrado'}), 404

    return jsonify(dict(analysis))


# ---------------------------------------------------------------------------
# Client routes
# ---------------------------------------------------------------------------

@app.route('/cliente')
@login_required
def client_dashboard():
    if session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))

    db = get_db()
    with db.cursor() as cur:
        cur.execute('''
            SELECT p.*,
                   (SELECT status FROM analyses WHERE project_id=p.id ORDER BY created_at DESC LIMIT 1) as last_status,
                   (SELECT total_issues FROM analyses WHERE project_id=p.id AND status='completed' ORDER BY completed_at DESC LIMIT 1) as total_issues,
                   (SELECT high_issues FROM analyses WHERE project_id=p.id AND status='completed' ORDER BY completed_at DESC LIMIT 1) as high_issues,
                   (SELECT completed_at FROM analyses WHERE project_id=p.id AND status='completed' ORDER BY completed_at DESC LIMIT 1) as last_analysis
            FROM projects p
            WHERE p.client_id = %s
            ORDER BY p.created_at DESC
        ''', (session['user_id'],))
        projects = cur.fetchall()

    return render_template('client/dashboard.html', projects=projects)


@app.route('/cliente/projeto/<int:project_id>')
@login_required
def client_project_view(project_id):
    if session.get('role') == 'admin':
        return redirect(url_for('admin_project_detail', project_id=project_id))

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            'SELECT * FROM projects WHERE id = %s AND client_id = %s',
            (project_id, session['user_id'])
        )
        project = cur.fetchone()

        if not project:
            flash('Projeto não encontrado.', 'danger')
            return redirect(url_for('client_dashboard'))

        cur.execute(
            "SELECT * FROM analyses WHERE project_id=%s AND status='completed' ORDER BY completed_at DESC LIMIT 1",
            (project_id,)
        )
        latest_analysis = cur.fetchone()

        pages_data = []
        if latest_analysis:
            cur.execute('''
                SELECT pg.*,
                       COUNT(i.id) as issue_count,
                       COUNT(CASE WHEN i.severity='high' THEN 1 END) as high_count,
                       COUNT(CASE WHEN i.severity='medium' THEN 1 END) as medium_count,
                       COUNT(CASE WHEN i.severity='low' THEN 1 END) as low_count
                FROM pages pg
                LEFT JOIN issues i ON i.page_id = pg.id
                WHERE pg.analysis_id = %s
                GROUP BY pg.id
                ORDER BY issue_count DESC
            ''', (latest_analysis['id'],))
            pages_data = cur.fetchall()

    return render_template('client/project_view.html',
                           project=project,
                           latest_analysis=latest_analysis,
                           pages_data=pages_data)


@app.route('/cliente/projeto/<int:project_id>/pagina/<int:page_id>')
@login_required
def client_page_issues(project_id, page_id):
    db = get_db()
    with db.cursor() as cur:
        # Validate ownership
        cur.execute('SELECT id FROM projects WHERE id=%s AND client_id=%s', (project_id, session['user_id']))
        if not cur.fetchone() and session.get('role') != 'admin':
            return jsonify({'error': 'Acesso negado'}), 403

        cur.execute('SELECT * FROM pages WHERE id = %s', (page_id,))
        page = cur.fetchone()

        cur.execute(
            'SELECT * FROM issues WHERE page_id = %s ORDER BY CASE severity WHEN %s THEN 1 WHEN %s THEN 2 ELSE 3 END',
            (page_id, 'high', 'medium')
        )
        issues = cur.fetchall()

    return jsonify({
        'page': dict(page),
        'issues': [dict(i) for i in issues]
    })


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    print('[INIT] Inicializando banco de dados...')
    init_db()
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=5000, debug=debug)
