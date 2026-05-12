# app.py
import os, threading, logging, sys
from flask import Flask, render_template, redirect, url_for, request, flash, send_from_directory, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_caching import Cache
from sqlalchemy import text
from models import db, User, TheoryBlock, TheoryTopic, TopicFile, Problem, ProblemFile, Message, RoleRequest, Favorite, \
    Notification, GroupChat, GroupMessage, PinnedChat, group_member

# ===== БЛОК 1: СОЗДАНИЕ ПРИЛОЖЕНИЯ (app создаётся ПЕРВЫМ!) =====
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-prod')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///olimp.db').replace('postgres://',
                                                                                                     'postgresql://')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 10, 'max_overflow': 5, 'pool_pre_ping': True,
    'pool_recycle': 1800, 'pool_timeout': 30
}
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ===== БЛОК 2: ИНИЦИАЛИЗАЦИЯ РАСШИРЕНИЙ =====
db.init_app(app)
login_manager = LoginManager();
login_manager.init_app(app);
login_manager.login_view = 'login'
cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 300})
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', logger=False, engineio_logger=False,
                    ping_timeout=10, ping_interval=25)


# ===== БЛОК 3: ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
@login_manager.user_loader
def load_user(user_id): return db.session.get(User, int(user_id))


ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx', 'txt', 'zip', 'rar'}


def allowed_file(filename): return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


online_users, sid_to_user, lock = set(), {}, threading.Lock()


def create_super_admin():
    if not User.query.filter_by(username='superadmin').first():
        sa = User(username='superadmin', password=generate_password_hash('super123'), email='superadmin@olimp.ru',
                  birth_date='01.01.2000', role='super_admin', agreed=True)
        db.session.add(sa);
        db.session.commit()
    if not GroupChat.query.first():
        db.session.add_all([GroupChat(name='Чат операторов', role_required='operator', is_custom=False),
                            GroupChat(name='Чат редакторов', role_required='editor', is_custom=False),
                            GroupChat(name='Чат админов', role_required='admin', is_custom=False)])
        db.session.commit()


# ===== БЛОК 4: РОУТЫ АВТОРИЗАЦИИ =====
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        username, password = request.form.get('username', '').strip(), request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user);
            return redirect(request.args.get('next') or url_for('index'))
        flash('Неверный логин или пароль', 'error')
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        username, password, confirm = request.form.get('username', '').strip(), request.form.get('password',
                                                                                                 ''), request.form.get(
            'confirm_password', '')
        email, birth_date = request.form.get('email', '').strip(), request.form.get('birth_date', '')
        agreed = request.form.get('agreed') == 'on'
        if not all([username, password, email, birth_date]): flash('Заполните все поля',
                                                                   'error'); return render_template('register.html')
        if password != confirm: flash('Пароли не совпадают', 'error'); return render_template('register.html')
        if len(password) < 6: flash('Пароль минимум 6 символов', 'error'); return render_template('register.html')
        if '@' not in email: flash('Некорректный email', 'error'); return render_template('register.html')
        if not agreed: flash('Примите условия', 'error'); return render_template('register.html')
        if User.query.filter_by(username=username).first() or User.query.filter_by(email=email).first(): flash(
            'Логин или email занят', 'error'); return render_template('register.html')
        new_user = User(username=username, password=generate_password_hash(password), email=email,
                        birth_date=birth_date, role='student', agreed=True)
        db.session.add(new_user);
        db.session.commit();
        login_user(new_user);
        flash('Регистрация успешна!', 'success')
        return redirect(url_for('index'))
    return render_template('register.html')


@app.route('/logout')
@login_required
def logout(): logout_user(); return redirect(url_for('index'))


# ===== БЛОК 5: ОСНОВНЫЕ РОУТЫ =====
@app.route('/')
@cache.cached(timeout=300)
def index(): return render_template('index.html')


@app.route('/profile')
@login_required
def profile(): return render_template('profile.html', user=current_user)


@app.route('/chat')
@login_required
def chat():
    role = current_user.role
    users = []
    if role == 'student':
        pinned = PinnedChat.query.filter_by(user_id=current_user.id).all()
        users = [db.session.get(User, rec.operator_id) for rec in pinned if db.session.get(User, rec.operator_id)]
    elif role in ['operator', 'super_admin']:
        users = User.query.filter(User.id != current_user.id).order_by(User.username).all()
    else:
        users = User.query.filter(User.role == role, User.id != current_user.id).order_by(User.username).all()

    same_role_users = User.query.filter(User.role == role, User.id != current_user.id).order_by(
        User.username).all() if role in ['operator', 'editor', 'admin'] else []
    fav_ids = {f.target_id for f in Favorite.query.filter_by(user_id=current_user.id).all()}
    system_groups = GroupChat.query.filter(GroupChat.is_custom == False, GroupChat.role_required.in_(
        [role, 'admin' if role == 'super_admin' else role])).all()
    custom_groups = current_user.joined_groups if hasattr(current_user, 'joined_groups') else []
    my_groups = list(set(system_groups + custom_groups))

    chat_user_id, group_id = request.args.get('user_id', type=int), request.args.get('group', type=int)
    messages, selected_group = [], None
    if group_id:
        selected_group = GroupChat.query.get_or_404(group_id)
        if selected_group.is_custom and current_user not in selected_group.members: flash('Нет доступа',
                                                                                          'error'); return redirect(
            url_for('chat'))
        messages = GroupMessage.query.filter_by(chat_id=group_id).order_by(GroupMessage.timestamp).all()
    elif chat_user_id:
        messages = Message.query.filter(
            ((Message.sender_id == current_user.id) & (Message.receiver_id == chat_user_id)) | (
                        (Message.sender_id == chat_user_id) & (Message.receiver_id == current_user.id))).order_by(
            Message.timestamp).all()
        for m in messages:
            if m.receiver_id == current_user.id: m.is_read = True
        db.session.commit()

    visible_online = set(online_users) if role == 'super_admin' else {uid for uid in online_users if
                                                                      db.session.get(User, uid) and db.session.get(User,
                                                                                                                   uid).role != 'super_admin'} if role in [
        'admin', 'operator', 'editor'] else set()
    return render_template('chat.html', users=users, messages=messages,
                           selected_user=db.session.get(User, chat_user_id) if chat_user_id else None,
                           selected_group=selected_group, fav_ids=fav_ids, online_users=visible_online,
                           my_groups=my_groups, same_role_users=same_role_users)


@app.route('/admin')
@login_required
def admin_panel():
    if current_user.role not in ['admin', 'super_admin']: flash('Нет доступа', 'error'); return redirect(
        url_for('index'))
    return render_template('admin_panel.html', users=User.query.all())


# ===== БЛОК 6: ДОПОЛНИТЕЛЬНЫЕ РОУТЫ =====
@app.route('/chat/pin', methods=['POST'])
@login_required
def pin_chat():
    if current_user.role != 'operator': return jsonify({'ok': False, 'error': 'Только операторы'}), 403
    target_id = request.form.get('user_id', type=int)
    if not target_id: return jsonify({'ok': False, 'error': 'Нет ID'}), 400
    target = db.session.get(User, target_id)
    if not target or target.role == 'operator': return jsonify({'ok': False, 'error': 'Неверный пользователь'}), 400
    existing = PinnedChat.query.filter_by(operator_id=current_user.id, user_id=target_id).first()
    if existing: db.session.delete(existing); db.session.commit(); return jsonify(
        {'ok': True, 'pinned': False, 'msg': 'Чат откреплён'})
    db.session.add(PinnedChat(operator_id=current_user.id, user_id=target_id));
    db.session.commit()
    return jsonify({'ok': True, 'pinned': True, 'msg': 'Чат закреплён'})


@app.route('/chat/favorite/toggle', methods=['POST'])
@login_required
def toggle_favorite():
    target_id = request.form.get('target_id', type=int)
    if not target_id: return jsonify({'ok': False}), 400
    existing = Favorite.query.filter_by(user_id=current_user.id, target_id=target_id).first()
    if existing:
        db.session.delete(existing)
    else:
        db.session.add(Favorite(user_id=current_user.id, target_id=target_id))
    db.session.commit();
    return jsonify({'ok': True})


@app.route('/chat/suggest_users')
@login_required
def suggest_users():
    if current_user.role == 'student': return jsonify([])
    users = User.query.filter(User.role == current_user.role, User.id != current_user.id).all()
    return jsonify([{'id': u.id, 'name': u.username} for u in users])


@app.route('/chat/create_group', methods=['POST'])
@login_required
def create_group():
    if current_user.role == 'student': return jsonify({'ok': False, 'error': 'Студенты не могут создавать группы'}), 403
    name, desc = request.form.get('name', '').strip(), request.form.get('description', '').strip()
    member_ids = request.form.getlist('members')
    if not name: return jsonify({'ok': False, 'error': 'Укажите название'}), 400
    avatar_name = None
    avatar_file = request.files.get('avatar')
    if avatar_file and avatar_file.filename and allowed_file(avatar_file.filename):
        avatar_name = f"group_{int(datetime.utcnow().timestamp())}_{secure_filename(avatar_file.filename)}"
        avatar_file.save(os.path.join(app.config['UPLOAD_FOLDER'], avatar_name))
    group = GroupChat(name=name, description=desc, avatar=avatar_name, creator_id=current_user.id,
                      role_required=current_user.role, is_custom=True)
    db.session.add(group);
    db.session.flush()
    members = [current_user] + [db.session.get(User, int(mid)) for mid in member_ids if
                                db.session.get(User, int(mid)) and db.session.get(User,
                                                                                  int(mid)).role == current_user.role and int(
                                    mid) != current_user.id]
    group.members.extend(members);
    db.session.commit()
    return jsonify({'ok': True, 'group_id': group.id})


@app.route('/notifications/mark_read', methods=['POST'])
@login_required
def mark_notifications_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({'is_read': True});
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/notifications/list')
@login_required
def list_notifications():
    notifs = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(
        20).all()
    return jsonify([{'id': n.id, 'title': n.title, 'message': n.message, 'link': n.link,
                     'time': n.created_at.strftime('%H:%M'), 'read': n.is_read} for n in notifs])


@app.route('/health')
def health():
    try:
        db.session.execute(text('SELECT 1')); db_ok = True
    except:
        db_ok = False
    return {'status': 'ok' if db_ok else 'error', 'database': 'connected' if db_ok else 'disconnected',
            'python_version': sys.version}, 200 if db_ok else 500


# ===== БЛОК 7: КОНТЕКСТ-ПРОЦЕССОР =====
@app.context_processor
def inject_globals():
    if current_user.is_authenticated:
        unread = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
        role = current_user.role
        groups = GroupChat.query.filter(
            GroupChat.role_required.in_([role, 'admin' if role == 'super_admin' else role])).all()
        return dict(unread_count=unread, my_groups=groups)
    return dict(unread_count=0, my_groups=[])


# ===== БЛОК 8: SOCKET.IO =====
@socketio.on('connect')
def handle_connect(): pass


@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    with lock:
        uid = sid_to_user.pop(sid, None)
        if uid: online_users.discard(uid); emit('user_status', {'user_id': uid, 'status': 'offline'}, broadcast=True)


@socketio.on('register_online')
def handle_register(data):
    uid = data.get('user_id')
    if uid:
        with lock: sid_to_user[request.sid] = uid; online_users.add(uid)
        emit('user_status', {'user_id': uid, 'status': 'online'}, broadcast=True, include_self=False)


@socketio.on('join_chat')
def handle_join_chat(data):
    s, o = data.get('sender_id'), data.get('other_id')
    try:
        s, o = (int(s) if s else None), (int(o) if o else None)
    except:
        return
    if not s or not o: return
    join_room(f"chat_{min(s, o)}_{max(s, o)}")


@socketio.on('send_message')
def handle_message(data):
    s, r, c = data.get('sender_id'), data.get('receiver_id'), data.get('content', '').strip()
    try:
        s, r = (int(s) if s else None), (int(r) if r else None)
    except:
        return
    if not all([s, r, c]): return
    with app.app_context():
        u = db.session.get(User, s)
        if not u: return
        m = Message(sender_id=s, receiver_id=r, content=c, timestamp=datetime.utcnow())
        db.session.add(m);
        db.session.commit()
        emit('new_message',
             {'sender_id': s, 'sender_name': u.username, 'content': c, 'timestamp': m.timestamp.strftime('%H:%M')},
             room=f"chat_{min(s, r)}_{max(s, r)}")
        if r != s:
            notif = Notification(user_id=r, title=f'Новое сообщение от {u.username}',
                                 message=c[:100] + ('...' if len(c) > 100 else ''), link=url_for('chat', user_id=s))
            db.session.add(notif);
            db.session.commit()
            emit('new_notification', {'title': notif.title, 'message': notif.message, 'link': notif.link},
                 room=f'user_{r}')


@socketio.on('join_group_room')
def handle_join_group(data):
    group_id = data.get('group_id')
    if not group_id: return
    group = GroupChat.query.get(group_id)
    if not group: return
    if group.is_custom:
        if current_user not in group.members: return
    else:
        allowed = [group.role_required]
        if current_user.role == 'super_admin': allowed.append('admin')
        if current_user.role not in allowed: return
    join_room(f"group_{group.id}")


@socketio.on('send_group_message')
def handle_group_msg(data):
    content, group_id = data.get('content', '').strip(), data.get('group_id')
    if not content or not group_id: return
    with app.app_context():
        chat = GroupChat.query.get(group_id)
        if not chat or (chat.is_custom and current_user not in chat.members): return
        msg = GroupMessage(chat_id=chat.id, sender_id=current_user.id, content=content)
        db.session.add(msg);
        db.session.commit()
        emit('new_group_message', {'sender_name': current_user.username, 'content': content,
                                   'timestamp': datetime.utcnow().strftime('%H:%M'), 'group_id': chat.id},
             room=f"group_{chat.id}")


# ===== БЛОК 9: ЗАПУСК =====
if __name__ == '__main__':
    with app.app_context():
        db.create_all();
        create_super_admin()
        with db.engine.connect() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_msg_sender ON message(sender_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_msg_receiver ON message(receiver_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_msg_time ON message(timestamp)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_user_role ON \"user\"(role)"))
            conn.commit()
        print("✅ Индексы применены")
    socketio.run(app, debug=False, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)