import os
import os, threading
from flask import Flask, render_template, redirect, url_for, request, flash, send_from_directory, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
from flask_socketio import SocketIO, emit, join_room, leave_room
from models import db, User, TheoryBlock, TheoryTopic, TopicFile, Problem, ProblemFile, Message, RoleRequest, Favorite, Notification, GroupChat, GroupMessage, PinnedChat


#ghbdtn


app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-prod')
# 🔹 Используем PostgreSQL если есть переменная, иначе SQLite для локалки
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///olimp.db').replace('postgres://', 'postgresql://')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ===== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ДЛЯ ВСЕХ ШАБЛОНОВ =====
@app.context_processor
def inject_globals():
    if current_user.is_authenticated:
        # Считаем непрочитанные уведомления
        unread = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()

        # Определяем доступные групповые чаты
        role = current_user.role
        groups = []
        if role == 'operator':
            groups = GroupChat.query.filter_by(role_required='operator').all()
        elif role == 'editor':
            groups = GroupChat.query.filter_by(role_required='editor').all()
        elif role in ['admin', 'super_admin']:
            groups = GroupChat.query.filter_by(role_required='admin').all()

        return dict(unread_count=unread, my_groups=groups)

    # Если пользователь не авторизован
    return dict(unread_count=0, my_groups=[])


# SocketIO (threading для стабильности на Python 3.13)
# ✅ Используем threading для совместимости с Python 3.14 на Render
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading',  # 🔹 Обязательно
    logger=False,
    engineio_logger=False
)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx', 'txt', 'zip', 'rar'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# Глобальные переменные для онлайн-трекинга
online_users = set()
sid_to_user = {}
lock = threading.Lock()

# Роли, имеющие доступ к групповым чатам
# Маппинг ролей на SocketIO-комнаты
ROLE_GROUPS = {
    'operator': 'room_operators',
    'editor': 'room_editors',
    'admin': 'room_admins',
    'super_admin': 'room_admins'
}


def create_super_admin():
    if not User.query.filter_by(username='superadmin').first():
        sa = User(username='superadmin', password=generate_password_hash('super123'),
                  email='superadmin@olimp.ru', birth_date='01.01.2000', role='super_admin', agreed=True)
        db.session.add(sa);
        db.session.commit()

    if not GroupChat.query.first():
        db.session.add(GroupChat(name='Чат операторов', role_required='operator', is_custom=False))
        db.session.add(GroupChat(name='Чат редакторов', role_required='editor', is_custom=False))
        db.session.add(GroupChat(name='Чат админов', role_required='admin', is_custom=False))
        db.session.commit()


# ===== МАРШРУТЫ =====
@app.route('/')
def index(): return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        u, p, cp, e, b, a = request.form.get('username', '').strip(), request.form.get('password',
                                                                                       ''), request.form.get(
            'confirm_password', ''), request.form.get('email', '').strip(), request.form.get('birth_date',
                                                                                             '').strip(), request.form.get(
            'agreed') == 'on'
        if not all([u, p, e, b]): flash('Заполните все поля', 'error'); return render_template('register.html')
        if p != cp: flash('Пароли не совпадают', 'error'); return render_template('register.html')
        if len(p) < 6: flash('Пароль минимум 6 символов', 'error'); return render_template('register.html')
        if '@' not in e: flash('Некорректный email', 'error'); return render_template('register.html')
        if not a: flash('Примите условия', 'error'); return render_template('register.html')
        if User.query.filter_by(username=u).first() or User.query.filter_by(email=e).first():
            flash('Логин/email занят', 'error');
            return render_template('register.html')
        db.session.add(
            User(username=u, password=generate_password_hash(p), email=e, birth_date=b, role='student', agreed=True))
        db.session.commit();
        login_user(User.query.filter_by(username=u).first())
        flash('Успешно!', 'success');
        return redirect(url_for('index'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        u = User.query.filter_by(username=request.form.get('username', '').strip()).first()
        if u and check_password_hash(u.password, request.form.get('password', '')):
            login_user(u);
            return redirect(url_for('index'))
        flash('Неверный логин или пароль', 'error')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout(): logout_user(); return redirect(url_for('index'))


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        np, cp, ne = request.form.get('new_password', ''), request.form.get('confirm_password', ''), request.form.get(
            'email', '').strip()
        if np:
            if np != cp: flash('Пароли не совпадают', 'error'); return render_template('profile.html',
                                                                                       user=current_user)
            if len(np) < 6: flash('Минимум 6 символов', 'error'); return render_template('profile.html',
                                                                                         user=current_user)
            current_user.password = generate_password_hash(np)
        if ne:
            if '@' not in ne: flash('Некорректный email', 'error'); return render_template('profile.html',
                                                                                           user=current_user)
            if User.query.filter(User.email == ne, User.id != current_user.id).first(): flash('Email занят',
                                                                                              'error'); return render_template(
                'profile.html', user=current_user)
            current_user.email = ne
        db.session.commit();
        flash('Обновлено', 'success')
    return render_template('profile.html', user=current_user)


@app.route('/theory')
def theory(): return render_template('theory.html', blocks=TheoryBlock.query.all())


@app.route('/theory/<int:block_id>/<int:topic_id>')
def theory_topic(block_id, topic_id): return render_template('theory_topic.html',
                                                             topic=TheoryTopic.query.get_or_404(topic_id))


@app.route('/theory/edit')
@login_required
def theory_edit():
    if current_user.role not in ['admin', 'super_admin', 'editor']: return redirect(url_for('index'))
    return render_template('theory_edit.html', blocks=TheoryBlock.query.all())


@app.route('/theory/add_block', methods=['POST'])
@login_required
def add_block():
    if current_user.role not in ['admin', 'super_admin', 'editor']: return redirect(url_for('index'))
    t = request.form.get('title', '').strip()
    if t: db.session.add(TheoryBlock(title=t)); db.session.commit()
    return redirect(url_for('theory_edit'))


@app.route('/theory/add_topic/<int:block_id>', methods=['POST'])
@login_required
def add_topic(block_id):
    if current_user.role not in ['admin', 'super_admin', 'editor']: return redirect(url_for('index'))
    t = request.form.get('title', '').strip()
    if t: db.session.add(TheoryTopic(title=t, block_id=block_id)); db.session.commit()
    return redirect(url_for('theory_edit'))


@app.route('/theory/upload_file/<int:topic_id>', methods=['POST'])
@login_required
def upload_file(topic_id):
    if current_user.role not in ['admin', 'super_admin', 'editor']: return redirect(url_for('index'))
    f = request.files.get('file')
    if f and f.filename and allowed_file(f.filename):
        fn = secure_filename(f.filename);
        f.save(os.path.join(app.config['UPLOAD_FOLDER'], fn))
        db.session.add(TopicFile(topic_id=topic_id, filename=fn, filepath=fn));
        db.session.commit()
    return redirect(url_for('theory_edit'))


@app.route('/theory/delete_block/<int:block_id>', methods=['POST'])
@login_required
def delete_block(block_id):
    if current_user.role not in ['admin', 'super_admin', 'editor']: return redirect(url_for('index'))
    b = TheoryBlock.query.get_or_404(block_id);
    db.session.delete(b);
    db.session.commit()
    return redirect(url_for('theory_edit'))


@app.route('/theory/delete_topic/<int:topic_id>', methods=['POST'])
@login_required
def delete_topic(topic_id):
    if current_user.role not in ['admin', 'super_admin', 'editor']: return redirect(url_for('index'))
    t = TheoryTopic.query.get_or_404(topic_id);
    db.session.delete(t);
    db.session.commit()
    return redirect(url_for('theory_edit'))


@app.route('/problems')
def problems():
    d = request.args.get('difficulty')
    return render_template('problem_bank.html',
                           problems=Problem.query.filter_by(difficulty=d).all() if d in ['easy', 'medium',
                                                                                         'hard'] else Problem.query.all())


@app.route('/problems/<int:problem_id>')
def problem_detail(problem_id): return render_template('problem_detail.html',
                                                       problem=Problem.query.get_or_404(problem_id))


@app.route('/problems/edit')
@login_required
def problem_bank_edit():
    if current_user.role not in ['admin', 'super_admin', 'editor']: return redirect(url_for('index'))
    return render_template('problem_bank_edit.html', problems=Problem.query.all())


@app.route('/problems/add', methods=['POST'])
@login_required
def add_problem():
    if current_user.role not in ['admin', 'super_admin', 'editor']: return redirect(url_for('index'))
    t, d, s, diff = request.form.get('title', '').strip(), request.form.get('description',
                                                                            '').strip(), request.form.get('solution',
                                                                                                          '').strip(), request.form.get(
        'difficulty', 'medium')
    if t and d: db.session.add(Problem(title=t, description=d, solution=s, difficulty=diff)); db.session.commit()
    return redirect(url_for('problem_bank_edit'))


@app.route('/problems/upload_file/<int:problem_id>', methods=['POST'])
@login_required
def upload_problem_file(problem_id):
    if current_user.role not in ['admin', 'super_admin', 'editor']: return redirect(url_for('index'))
    f = request.files.get('file')
    if f and f.filename and allowed_file(f.filename):
        fn = secure_filename(f.filename);
        f.save(os.path.join(app.config['UPLOAD_FOLDER'], fn))
        db.session.add(ProblemFile(problem_id=problem_id, filename=fn, filepath=fn));
        db.session.commit()
    return redirect(url_for('problem_bank_edit'))


@app.route('/problems/delete/<int:problem_id>', methods=['POST'])
@login_required
def delete_problem(problem_id):
    if current_user.role not in ['admin', 'super_admin', 'editor']: return redirect(url_for('index'))
    db.session.delete(Problem.query.get_or_404(problem_id));
    db.session.commit()
    return redirect(url_for('problem_bank_edit'))


@app.route('/uploads/<path:filename>')
def uploaded_file(filename): return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/chat')
@login_required
def chat():
    role = current_user.role
    users = []

    # 🔹 1. ФОРМИРУЕМ СПИСОК "ЛИЧНЫХ" СТРОГО ПО ТЗ
    if role == 'student':
        # Ученики видят ТОЛЬКО операторов, которые их закрепили
        pinned = PinnedChat.query.filter_by(user_id=current_user.id).all()
        users = [db.session.get(User, rec.operator_id) for rec in pinned]
        users = [u for u in users if u]  # убираем None, если оператор удалён

    elif role in ['operator', 'super_admin']:
        # Операторы и супер-админы видят ВСЕХ пользователей
        users = User.query.filter(User.id != current_user.id).order_by(User.username).all()

    else:
        # Остальные роли (editor, admin и т.д.) видят ТОЛЬКО коллег с такой же ролью
        users = User.query.filter(User.role == role, User.id != current_user.id).order_by(User.username).all()

    # 🔹 2. Данные для вкладки "Команды"
    same_role_users = User.query.filter(User.role == role, User.id != current_user.id).order_by(User.username).all() \
        if role in ['operator', 'editor', 'admin'] else []

    fav_ids = {f.target_id for f in Favorite.query.filter_by(user_id=current_user.id).all()}
    # Системные группы по роли
    system_groups = GroupChat.query.filter(
        GroupChat.is_custom == False,
        GroupChat.role_required.in_([current_user.role, 'admin' if current_user.role == 'super_admin' else current_user.role])
    ).all()
    # Пользовательские группы, где состоит текущий юзер
    custom_groups = current_user.joined_groups if hasattr(current_user, 'joined_groups') else []
    my_groups = list(set(system_groups + custom_groups))

    chat_user_id = request.args.get('user_id', type=int)
    group_id = request.args.get('group', type=int)
    messages, selected_group = [], None

    if group_id:
        selected_group = GroupChat.query.get_or_404(group_id)
        messages = GroupMessage.query.filter_by(chat_id=group_id).order_by(GroupMessage.timestamp).all()
    elif chat_user_id:
        messages = Message.query.filter(
            ((Message.sender_id == current_user.id) & (Message.receiver_id == chat_user_id)) |
            ((Message.sender_id == chat_user_id) & (Message.receiver_id == current_user.id))
        ).order_by(Message.timestamp).all()
        for m in messages:
            if m.receiver_id == current_user.id: m.is_read = True
        db.session.commit()

    visible_online = set(online_users)

    return render_template('chat.html',
                           users=users,
                           messages=messages,
                           selected_user=db.session.get(User, chat_user_id) if chat_user_id else None,
                           selected_group=selected_group,
                           fav_ids=fav_ids,
                           online_users=visible_online,
                           my_groups=my_groups,
                           same_role_users=same_role_users) # 🔹 Передаём закреплённых операторов # 🔹 Передаём закреплённые чаты


# 🔹 Оператор закрепляет чат с пользователем
@app.route('/chat/pin', methods=['POST'])
@login_required
def pin_chat():
    if current_user.role != 'operator':
        return jsonify({'ok': False, 'error': 'Доступ только для операторов'}), 403

    target_id = request.form.get('user_id', type=int)
    if not target_id: return jsonify({'ok': False, 'error': 'Нет ID'}), 400

    target = User.query.get(target_id)
    if not target or target.role == 'operator':
        return jsonify({'ok': False, 'error': 'Неверный пользователь'}), 400

    # Переключение: если закреплено → убрать, если нет → закрепить
    existing = PinnedChat.query.filter_by(operator_id=current_user.id, user_id=target_id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        return jsonify({'ok': True, 'pinned': False, 'msg': 'Чат откреплён'})
    else:
        db.session.add(PinnedChat(operator_id=current_user.id, user_id=target_id))
        db.session.commit()
        return jsonify({'ok': True, 'pinned': True, 'msg': 'Чат закреплён у пользователя'})


@app.route('/chat/suggest_users')
@login_required
def suggest_users():
    # Возвращает пользователей с той же ролью (кроме студентов и себя)
    if current_user.role == 'student':
        return jsonify([])
    users = User.query.filter(User.role == current_user.role, User.id != current_user.id).all()
    return jsonify([{'id': u.id, 'name': u.username} for u in users])


@app.route('/chat/create_group', methods=['POST'])
@login_required
def create_group():
    if current_user.role == 'student':
        return jsonify({'ok': False, 'error': 'Студенты не могут создавать группы'}), 403

    name = request.form.get('name', '').strip()
    desc = request.form.get('description', '').strip()
    member_ids = request.form.getlist('members')

    if not name:
        return jsonify({'ok': False, 'error': 'Укажите название группы'}), 400

    avatar_name = None
    avatar_file = request.files.get('avatar')
    if avatar_file and avatar_file.filename and allowed_file(avatar_file.filename):
        avatar_name = f"group_{int(datetime.utcnow().timestamp())}_{secure_filename(avatar_file.filename)}"
        avatar_file.save(os.path.join(app.config['UPLOAD_FOLDER'], avatar_name))

    group = GroupChat(
        name=name, description=desc, avatar=avatar_name,
        creator_id=current_user.id, role_required=current_user.role, is_custom=True
    )
    db.session.add(group)
    db.session.flush()  # Чтобы получить group.id

    # Добавляем создателя и выбранных участников
    members = [current_user]
    for mid in member_ids:
        u = db.session.get(User, int(mid))
        if u and u.role == current_user.role and u.id != current_user.id:
            members.append(u)
    group.members.extend(members)
    db.session.commit()

    return jsonify({'ok': True, 'group_id': group.id})




@app.route('/chat/favorite/toggle', methods=['POST'])
@login_required
def toggle_favorite():
    tid = request.form.get('target_id', type=int)
    if not tid: return jsonify({'ok': False})
    fav = Favorite.query.filter_by(user_id=current_user.id, target_id=tid).first()
    if fav:
        db.session.delete(fav); msg = 'Удалено из избранного'
    else:
        db.session.add(Favorite(user_id=current_user.id, target_id=tid)); msg = 'Добавлено в избранное'
    db.session.commit()
    return jsonify({'ok': True, 'msg': msg})


@app.route('/chat/send', methods=['POST'])
@login_required
def send_message():
    receiver_id = request.form.get('receiver_id', type=int)
    file = request.files.get('file')
    if file and file.filename and allowed_file(file.filename):
        fn = secure_filename(file.filename);
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], fn))
        db.session.add(Message(sender_id=current_user.id, receiver_id=receiver_id, filename=fn, filepath=fn,
                               timestamp=datetime.utcnow()))
        db.session.commit()
    return redirect(url_for('chat', user_id=receiver_id))


# 🔹 УВЕДОМЛЕНИЯ
@app.route('/notifications/mark_read', methods=['POST'])
@login_required
def mark_notifications_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({'is_read': True})
    db.session.commit()
    return jsonify({'ok': True})


@app.route('/notifications/list')
@login_required
def list_notifications():
    notifs = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(
        20).all()
    return jsonify([{
        'id': n.id,
        'title': n.title,
        'message': n.message,
        'link': n.link,
        'time': n.created_at.strftime('%H:%M'),
        'read': n.is_read
    } for n in notifs])


# 🔹 ГРУППОВЫЕ ЧАТЫ — отправка сообщения
@app.route('/group_chat/send', methods=['POST'])
@login_required
def send_group_message():
    chat_id = request.form.get('chat_id', type=int)
    content = request.form.get('content', '').strip()
    if not chat_id or not content:
        return redirect(url_for('chat'))

    chat = GroupChat.query.get_or_404(chat_id)
    if chat.role_required not in [current_user.role, 'admin' if current_user.role == 'super_admin' else None]:
        flash('Нет доступа', 'error')
        return redirect(url_for('chat'))

    msg = GroupMessage(chat_id=chat_id, sender_id=current_user.id, content=content)
    db.session.add(msg)
    db.session.commit()

    # 🔔 Отправляем через SocketIO всем в комнате
    room = ROLE_GROUPS.get(chat.role_required, f'room_{chat.role_required}')
    with app.app_context():
        emit('new_group_message', {
            'chat_id': chat_id,
            'sender_name': current_user.username,
            'content': content,
            'timestamp': msg.timestamp.strftime('%H:%M')
        }, room=room)

    return redirect(url_for('chat', group=chat_id))

# ===== АДМИН-ПАНЕЛЬ & РОЛИ =====
@app.route('/admin')
@login_required
def admin_panel():
    if current_user.role not in ['admin', 'super_admin']: return redirect(url_for('index'))
    users = User.query.all()
    reqs = RoleRequest.query.filter_by(status='pending').all() if current_user.role == 'super_admin' else []
    my_reqs = RoleRequest.query.filter_by(requester_id=current_user.id, status='pending').all()
    return render_template('admin_panel.html', users=users, requests=reqs, my_requests=my_reqs)


# 🔹 Запрос на смену роли (только admin)
@app.route('/admin/request_role/<int:user_id>', methods=['POST'])
@login_required
def request_role(user_id):
    if current_user.role != 'admin': return redirect(url_for('admin_panel'))

    target = User.query.get_or_404(user_id)
    new_role = request.form.get('role')

    if new_role == 'super_admin' or target.role == 'super_admin' or user_id == current_user.id:
        flash('❌ Роль супер-админа неизменна', 'error');
        return redirect(url_for('admin_panel'))

    if RoleRequest.query.filter_by(target_id=user_id, status='pending').first():
        flash('Запрос уже есть', 'info');
        return redirect(url_for('admin_panel'))

    # ✅ operator ДОБАВЛЕН в список
    if new_role in ['student', 'operator', 'editor', 'admin']:
        db.session.add(RoleRequest(requester_id=current_user.id, target_id=user_id, requested_role=new_role))
        db.session.commit()
        flash(f'✅ Запрос на {new_role} отправлен супер-админу', 'success')
    else:
        flash('❌ Недопустимая роль', 'error')
    return redirect(url_for('admin_panel'))


@app.route('/admin/approve_request/<int:req_id>', methods=['POST'])
@login_required
def approve_request(req_id):
    if current_user.role != 'super_admin': return redirect(url_for('index'))
    r = RoleRequest.query.get_or_404(req_id)
    target = User.query.get(r.target_id)

    # ✅ operator ДОБАВЛЕН в проверку
    if target and target.role != 'super_admin' and r.requested_role in ['student', 'operator', 'editor', 'admin']:
        r.status = 'approved'
        target.role = r.requested_role
        db.session.commit()
        flash(f'✅ Роль {target.username} изменена на {r.requested_role}', 'success')
    else:
        r.status = 'denied';
        db.session.commit()
        flash('❌ Запрос отклонён', 'error')
    return redirect(url_for('admin_panel'))


@app.route('/admin/deny_request/<int:req_id>', methods=['POST'])
@login_required
def deny_request(req_id):
    if current_user.role != 'super_admin': return redirect(url_for('index'))
    r = RoleRequest.query.get_or_404(req_id);
    r.status = 'denied';
    db.session.commit()
    flash('Отклонено', 'warning');
    return redirect(url_for('admin_panel'))


# 🔹 Прямое изменение роли (только super_admin)
@app.route('/admin/change_role_direct/<int:user_id>', methods=['POST'])
@login_required
def change_role_direct(user_id):
    if current_user.role != 'super_admin': return redirect(url_for('index'))
    if user_id == current_user.id:
        flash('❌ Нельзя изменить свою роль', 'error');
        return redirect(url_for('admin_panel'))

    target = User.query.get_or_404(user_id)
    new_role = request.form.get('role')

    if target.role == 'super_admin':
        flash('❌ Роль супер-админа не может быть изменена', 'error')
        return redirect(url_for('admin_panel'))
    if new_role == 'super_admin':
        flash('❌ Роль супер-админа назначается только через БД', 'error')
        return redirect(url_for('admin_panel'))

    # ✅ operator ДОБАВЛЕН в список
    if new_role in ['student', 'operator', 'editor', 'admin']:
        target.role = new_role
        db.session.commit()
        flash(f'✅ Роль {target.username} изменена на {new_role}', 'success')
    else:
        flash('❌ Недопустимая роль', 'error')
    return redirect(url_for('admin_panel'))


# ===== SOCKET.IO =====
@socketio.on('connect')
def handle_connect(): pass


@socketio.on('register_online')
def handle_register(data):
    uid = data.get('user_id')
    if uid:
        with lock:
            sid_to_user[request.sid] = uid
            online_users.add(uid)
        # Рассылаем статус всем, кто имеет право видеть этого пользователя
        emit('user_status', {'user_id': uid, 'status': 'online'}, broadcast=True, include_self=False)


@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    with lock:
        uid = sid_to_user.pop(sid, None)
        if uid:
            online_users.discard(uid)
            emit('user_status', {'user_id': uid, 'status': 'offline'}, broadcast=True)


@socketio.on('join_chat')
def handle_join_chat(data):
    s = data.get('sender_id')
    o = data.get('other_id')
    try:
        s = int(s) if s is not None else None
        o = int(o) if o is not None else None
    except (ValueError, TypeError):
        return
    if not s or not o:
        return
    room = f"chat_{min(s, o)}_{max(s, o)}"
    join_room(room)


@socketio.on('send_message')
def handle_message(data):
    s = data.get('sender_id')
    r = data.get('receiver_id')
    c = data.get('content', '').strip()
    try:
        s = int(s) if s is not None else None
        r = int(r) if r is not None else None
    except (ValueError, TypeError):
        return
    if not all([s, r, c]):
        return

    with app.app_context():
        u = User.query.get(s)
        if not u:
            return

        m = Message(sender_id=s, receiver_id=r, content=c, timestamp=datetime.utcnow())
        db.session.add(m)
        db.session.commit()

        room = f"chat_{min(s, r)}_{max(s, r)}"
        emit('new_message', {
            'sender_id': s,
            'sender_name': u.username,
            'content': c,
            'timestamp': m.timestamp.strftime('%H:%M')
        }, room=room)

        # 🔔 Уведомление получателю
        if r != s:
            notif = Notification(
                user_id=r,
                title=f'Новое сообщение от {u.username}',
                message=c[:100] + ('...' if len(c) > 100 else ''),
                link=url_for('chat', user_id=s)
            )
            db.session.add(notif)
            db.session.commit()
            emit('new_notification', {
                'title': notif.title,
                'message': notif.message,
                'link': notif.link
            }, room=f'user_{r}')


@socketio.on('join_group_room')
def handle_join_group(data):
    group_id = data.get('group_id')
    if not group_id: return

    group = GroupChat.query.get(group_id)
    if not group: return

    # Проверка доступа
    if group.is_custom:
        if current_user not in group.members: return
    else:
        allowed = [group.role_required]
        if current_user.role == 'super_admin': allowed.append('admin')
        if current_user.role not in allowed: return

    room = f"group_{group.id}"
    join_room(room)


@socketio.on('send_group_message')
def handle_group_msg(data):
    role = data.get('role')
    content = data.get('content', '').strip()
    group_id = data.get('group_id')  # 🔹 Добавлено: принимаем ID группы

    if not content or not role:
        return

    with app.app_context():
        # 🔹 Если передан group_id — ищем конкретную группу
        if group_id:
            chat = GroupChat.query.get(group_id)
            if not chat:
                return
            # Проверка: пользователь должен быть участником кастомной группы
            if chat.is_custom and current_user not in chat.members:
                return
            room = f"group_{chat.id}"
        else:
            # 🔹 Старая логика: поиск группы по роли (для системных)
            chat = GroupChat.query.filter_by(role_required=role, is_custom=False).first()
            if not chat:
                return
            room = f"group_{chat.id}"

        msg = GroupMessage(chat_id=chat.id, sender_id=current_user.id, content=content)
        db.session.add(msg)
        db.session.commit()

        emit('new_group_message', {
            'sender_name': current_user.username,
            'content': content,
            'timestamp': datetime.utcnow().strftime('%H:%M'),
            'group_id': chat.id  # 🔹 Передаём ID для корректной маршрутизации
        }, room=room)

@app.context_processor
def inject_globals():
    if current_user.is_authenticated:
        unread = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
        role = current_user.role
        groups = GroupChat.query.filter(GroupChat.role_required.in_([role, 'admin' if role == 'super_admin' else role])).all()
        return dict(unread_count=unread, my_groups=groups)
    return dict(unread_count=0, my_groups=[])

# ===== ЗАПУСК =====
with app.app_context():
    db.create_all()
    create_super_admin()

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)
