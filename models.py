# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False,index=True)
    password = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    birth_date = db.Column(db.String(20), nullable=False)
    role = db.Column(db.String(20), default='student',index=True)  # student, operator, editor, admin, super_admin
    agreed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    messages_sent = db.relationship('Message', foreign_keys='Message.sender_id', backref='sender', lazy=True)
    messages_received = db.relationship('Message', foreign_keys='Message.receiver_id', backref='receiver', lazy=True)
    notifications = db.relationship('Notification', backref='user', lazy=True, cascade='all, delete-orphan')
    group_messages = db.relationship('GroupMessage', backref='sender', lazy=True)

class TheoryBlock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    topics = db.relationship('TheoryTopic', backref='block', lazy=True, cascade='all, delete-orphan')

class TheoryTopic(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    block_id = db.Column(db.Integer, db.ForeignKey('theory_block.id'), nullable=False)
    files = db.relationship('TopicFile', backref='topic', lazy=True, cascade='all, delete-orphan')

class TopicFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    topic_id = db.Column(db.Integer, db.ForeignKey('theory_topic.id'), nullable=False)
    filename = db.Column(db.String(200), nullable=False)
    filepath = db.Column(db.String(500), nullable=False)

class Problem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    solution = db.Column(db.Text, nullable=True)
    difficulty = db.Column(db.String(20), default='medium')
    files = db.relationship('ProblemFile', backref='problem', lazy=True, cascade='all, delete-orphan')

class ProblemFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    problem_id = db.Column(db.Integer, db.ForeignKey('problem.id'), nullable=False)
    filename = db.Column(db.String(200), nullable=False)
    filepath = db.Column(db.String(500), nullable=False)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False,index=True)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False,index=True)
    content = db.Column(db.Text, nullable=True)
    filename = db.Column(db.String(200), nullable=True)
    filepath = db.Column(db.String(500), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow,index=True)
    is_read = db.Column(db.Boolean, default=False)

class RoleRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    target_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    requested_role = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    requester = db.relationship('User', foreign_keys=[requester_id], backref='role_requests_sent', lazy=True)
    target = db.relationship('User', foreign_keys=[target_id], backref='role_requests_received', lazy=True)

class Favorite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    target_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    __table_args__ = (db.UniqueConstraint('user_id', 'target_id', name='uq_favorite'),)
    user = db.relationship('User', foreign_keys=[user_id], backref='favorites_made')
    target = db.relationship('User', foreign_keys=[target_id], backref='favorited_by')

# 🔹 УВЕДОМЛЕНИЯ
class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    message = db.Column(db.Text, nullable=False)
    link = db.Column(db.String(200), nullable=True)  # URL для перехода
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# 🔹 Таблица связей для участников групп (добавьте ПЕРЕД классом GroupChat)
group_member = db.Table('group_member',
                        db.Column('group_id', db.Integer, db.ForeignKey('group_chat.id'), primary_key=True),
                        db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True)
                        )


class GroupChat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    avatar = db.Column(db.String(200), nullable=True)

    # 🔹 КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: nullable=True (разрешаем NULL для системных групп)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    role_required = db.Column(db.String(20), nullable=False)
    is_custom = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    creator = db.relationship('User', foreign_keys=[creator_id], backref='created_groups', lazy=True)
    members = db.relationship('User', secondary=group_member, backref='joined_groups', lazy=True)
    messages = db.relationship('GroupMessage', backref='chat', lazy=True, cascade='all, delete-orphan')


class GroupMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.Integer, db.ForeignKey('group_chat.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


# 🔹 Закреплённые чаты (оператор ↔ пользователь)
class PinnedChat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    operator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('operator_id', 'user_id', name='uq_pinned'),)

    operator = db.relationship('User', foreign_keys=[operator_id], backref='pinned_chats_as_operator')
    user = db.relationship('User', foreign_keys=[user_id], backref='pinned_chats_as_user')