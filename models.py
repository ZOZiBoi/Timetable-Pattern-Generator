from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
import json

db = SQLAlchemy()

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    google_id = db.Column(db.String(255), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(255), nullable=True)
    picture = db.Column(db.Text, nullable=True)
    refresh_token = db.Column(db.Text, nullable=True)
    
    calendar_type = db.Column(db.String(50), default='primary')
    calendar_color_id = db.Column(db.String(10), default='7')
    
    courses = db.relationship('UserCourse', backref='user', lazy=True, cascade="all, delete-orphan")

class UserCourse(db.Model):
    __tablename__ = 'user_courses'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    course_title = db.Column(db.String(255), nullable=False)
    section = db.Column(db.String(50), nullable=True)
    category = db.Column(db.String(100), nullable=True)
    credit_hours = db.Column(db.Integer, default=3)
    is_lab = db.Column(db.Boolean, default=False)
    instructor = db.Column(db.String(255), nullable=True)
    slots_json = db.Column(db.Text, nullable=True) # Store slots as JSON string
    
    def get_slots(self):
        if self.slots_json:
            return json.loads(self.slots_json)
        return []
    
    def set_slots(self, slots_list):
        self.slots_json = json.dumps(slots_list)
