import os
import json
import PyPDF2
import urllib.request
import urllib.error
import shutil
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///edu.db'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['PROFILE_PIC_FOLDER'] = 'static/profile_pics'

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- DATABASE MODELS ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    full_name = db.Column(db.String(150))
    bio = db.Column(db.String(255), default="New Scholar")
    profile_pic = db.Column(db.String(200), default="default.png")
    points = db.Column(db.Integer, default=0)
    quizzes_created = db.Column(db.Integer, default=0)
    total_score = db.Column(db.Float, default=0.0)
    active_theme = db.Column(db.String(50), default='theme-default')
    
    # Relationships
    inventory = db.relationship('UserItem', backref='owner', lazy=True)
    activities = db.relationship('QuizActivity', backref='user', lazy=True)
    notifications = db.relationship('Notification', backref='user', lazy=True) # <-- THIS WAS MISSING!

class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    files = db.relationship('UploadedFile', backref='subject', lazy=True)

class UploadedFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(200), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class StoreItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(255))
    price = db.Column(db.Integer, nullable=False)
    theme_class = db.Column(db.String(50)) 
    icon = db.Column(db.String(10))

class UserItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey('store_item.id'), nullable=False)
    item = db.relationship('StoreItem')

class QuizActivity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    score = db.Column(db.Integer)
    point_gain = db.Column(db.Integer)
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message = db.Column(db.String(255))
    is_read = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- HELPERS ---
def add_notification(user_id, message):
    notif = Notification(user_id=user_id, message=message)
    db.session.add(notif)
    db.session.commit()

def extract_text_from_pdf(pdf_path):
    text = ""
    try:
        with open(pdf_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted: text += extracted + "\n"
    except: pass
    return text

def generate_quiz_questions(text):
    api_key = os.environ.get("GEMINI_API_KEY")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    
    # Reinforced prompt forcing a clean, flat JSON structure
    prompt = (
        "Generate exactly 10 multiple-choice questions based on the following text. "
        "Your response must be ONLY a raw JSON array of objects. Do not include markdown blocks, code blocks, or text outside the array. "
        "Format: [{\"q\": \"Question?\", \"options\": [\"A\", \"B\", \"C\", \"D\"], \"answer\": 0}]. "
        f"Text material: {text[:8000]}"
    )
    
    data = {"contents": [{"parts":[{"text": prompt}]}]}
    req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'})
    
    try:
        with urllib.request.urlopen(req) as response:
            res = json.loads(response.read().decode('utf-8'))
            ai_raw_text = res['candidates'][0]['content']['parts'][0]['text'].strip()
            
            # Clean off any unexpected markdown wrappers the AI might add
            clean = ai_raw_text.replace('```json', '').replace('```', '').strip()
            raw_data = json.loads(clean)
            
            # Fix: If the AI wrapped the array inside an object (e.g., {"questions": [...]})
            if isinstance(raw_data, dict):
                for key in ['questions', 'quiz', 'results', 'data']:
                    if key in raw_data:
                        raw_data = raw_data[key]
                        break
            
            # Fix: Normalize keys so JavaScript ALWAYS gets a standard 'q'
            normalized_questions = []
            for item in raw_data:
                q_text = item.get('q') or item.get('question')
                opts = item.get('options') or item.get('choices')
                ans = item.get('answer') or item.get('correct_answer') or 0
                
                if q_text and opts:
                    normalized_questions.append({
                        'q': q_text,
                        'options': opts,
                        'answer': int(ans)
                    })
            return normalized_questions
            
    except Exception as e:
        print(f"--- AI ERROR: {e} ---")
        return []

# --- ROUTES ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username, password = request.form.get('username'), request.form.get('password')
        full_name, bio = request.form.get('full_name'), request.form.get('bio')
        file = request.files.get('profile_pic')
        
        if User.query.filter_by(username=username).first():
            flash('Username exists!', 'error')
            return redirect(url_for('register'))
            
        pic_filename = "default.png"
        if file and file.filename:
            pic_filename = secure_filename(f"{username}_{file.filename}")
            file.save(os.path.join(app.config['PROFILE_PIC_FOLDER'], pic_filename))

        new_user = User(username=username, password=generate_password_hash(password), 
                        full_name=full_name, bio=bio, profile_pic=pic_filename)
        db.session.add(new_user)
        db.session.commit()
        
        # Welcome notification
        add_notification(new_user.id, "Welcome to EduWay! Upload a PDF to start earning points. 🎉")
        
        login_user(new_user)
        return redirect(url_for('dashboard'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid login', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/', methods=['GET', 'POST'])
@login_required
def dashboard():
    if request.method == 'POST':
        name = request.form.get('subject_name')
        if name:
            db.session.add(Subject(name=name))
            current_user.quizzes_created += 1
            db.session.commit()
            os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(name)), exist_ok=True)
            
    leaderboard = User.query.order_by(User.points.desc()).limit(6).all()
    rank = User.query.filter(User.points > current_user.points).count() + 1
    recent_matches = QuizActivity.query.filter_by(user_id=current_user.id).order_by(QuizActivity.timestamp.desc()).limit(3).all()
    
    return render_template('dashboard.html', subjects=Subject.query.all(), 
                           leaderboard=leaderboard, world_rank=rank, recent_matches=recent_matches)

@app.route('/subject/<int:subject_id>', methods=['GET', 'POST'])
@login_required
def subject(subject_id):
    sub = Subject.query.get_or_404(subject_id)
    if request.method == 'POST' and 'file' in request.files:
        file = request.files['file']
        if file.filename:
            fname = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(sub.name), fname))
            db.session.add(UploadedFile(filename=fname, subject_id=sub.id, user_id=current_user.id))
            current_user.points += 1
            db.session.commit()
    return render_template('subject.html', subject=sub)

@app.route('/quiz/<int:subject_id>')
@login_required
def quiz(subject_id):
    sub = Subject.query.get_or_404(subject_id)
    path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(sub.name))
    text = "".join([extract_text_from_pdf(os.path.join(path, f.filename)) for f in sub.files])
    return render_template('quiz.html', subject=sub, questions=generate_quiz_questions(text))

@app.route('/finish_quiz', methods=['POST'])
@login_required
def finish_quiz():
    data = request.get_json()
    correct, total = data.get('correct', 0), data.get('total', 10)
    
    pts = correct // 2
    
    current_user.points += pts
    
    # Update Average Score
    current_user.total_score = (current_user.total_score + (correct/total*100)) / 2 if current_user.total_score > 0 else (correct/total*100)
    
    db.session.add(QuizActivity(user_id=current_user.id, score=correct, point_gain=pts))
    
    # --- UPDATED NOTIFICATION LOGIC ---
    if correct == total:
        msg = f"Perfect Score! 🏆 Gained {pts} QP."
    elif pts > 0:
        msg = f"Quiz passed! Gained {pts} QP."
    else:
        msg = f"Quiz finished. You scored {correct}/{total}. Keep studying to earn points! 📚"
        
    add_notification(current_user.id, msg)
    db.session.commit()
    
    return {"success": True, "points_earned": pts, "total_points": current_user.points}
@app.route('/store')
@login_required
def store():
    owned_ids = [inv.item_id for inv in current_user.inventory]
    return render_template('store.html', items=StoreItem.query.all(), owned_ids=owned_ids)

@app.route('/buy/<int:item_id>', methods=['POST'])
@login_required
def buy(item_id):
    item = StoreItem.query.get_or_404(item_id)
    if current_user.points >= item.price:
        current_user.points -= item.price
        db.session.add(UserItem(user_id=current_user.id, item_id=item.id))
        add_notification(current_user.id, f"Purchased {item.name} theme! ✨")
        db.session.commit()
    else:
        flash('Not enough points!', 'error')
    return redirect(url_for('store'))

@app.route('/equip/<int:item_id>', methods=['POST'])
@login_required
def equip(item_id):
    item = StoreItem.query.get_or_404(item_id)
    current_user.active_theme = item.theme_class
    db.session.commit()
    return redirect(url_for('store'))

@app.route('/equip_default', methods=['POST'])
def equip_default():
    current_user.active_theme = 'theme-default'
    db.session.commit()
    return redirect(url_for('store'))

@app.route('/delete_subject/<int:subject_id>', methods=['POST'])
def delete_subject(subject_id):
    sub = Subject.query.get_or_404(subject_id)
    shutil.rmtree(os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(sub.name)), ignore_errors=True)
    UploadedFile.query.filter_by(subject_id=sub.id).delete()
    db.session.delete(sub)
    db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/delete_file/<int:file_id>', methods=['POST'])
@login_required
def delete_file(file_id):
    file = UploadedFile.query.get_or_404(file_id)
    subject = Subject.query.get(file.subject_id)
    f_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(subject.name), file.filename)
    if os.path.exists(f_path): os.remove(f_path)
    db.session.delete(file)
    db.session.commit()
    return redirect(url_for('subject', subject_id=subject.id))

@app.route('/leaderboard')
@login_required
def full_leaderboard():
    return render_template('leaderboard.html', users=User.query.order_by(User.points.desc()).all())

@app.route('/analytics')
@login_required
def analytics():
    recent_scores = [a.score for a in current_user.activities[-10:]]
    avg_score = sum(recent_scores) / len(recent_scores) if recent_scores else 0
    return render_template('analytics.html', avg_score=avg_score)

@app.route('/clear_notifications', methods=['POST'])
@login_required
def clear_notifications():
    Notification.query.filter_by(user_id=current_user.id).update({Notification.is_read: True})
    db.session.commit()
    return redirect(request.referrer)

def init_store():
    if StoreItem.query.count() == 0:
        items = [
            StoreItem(name="Midnight Pro", price=15, theme_class="theme-dark", icon="🌙"),
            StoreItem(name="Hacker Terminal", price=30, theme_class="theme-hacker", icon="💻"),
            StoreItem(name="Sakura Dawn", price=20, theme_class="theme-blossom", icon="🌸"),
            StoreItem(name="Ocean Breeze", price=20, theme_class="theme-ocean", icon="🌊"),
            StoreItem(name="Coffee Shop", price=25, theme_class="theme-coffee", icon="☕"),
            StoreItem(name="Sunset Glow", price=35, theme_class="theme-sunset", icon="🌅"),
            StoreItem(name="Cyberpunk 2077", price=50, theme_class="theme-cyber", icon="🤖")
        ]
        db.session.add_all(items)
        db.session.commit()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        init_store()
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        os.makedirs(app.config['PROFILE_PIC_FOLDER'], exist_ok=True)
    app.run(debug=True)