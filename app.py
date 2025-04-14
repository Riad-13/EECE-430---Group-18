"""
app.py

A simple Flask application demonstrating:
- Three user roles: Doctor, Patient, Receptionist
- SQLite database (using SQLAlchemy)
- Login, Signup, Logout
- Appointment scheduling, canceling, rescheduling
- Basic chat between Patient and Receptionist
"""

from flask import Flask, render_template, redirect, url_for, request, session, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from passlib.hash import pbkdf2_sha256
from sqlalchemy.orm import relationship     
app = Flask(__name__)
app.secret_key = "secret_key_for_session"  # Change in production

# Configure SQLite database
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///hospital.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# Add datetime.now to template context
@app.context_processor
def utility_processor():
    return dict(now=datetime.now)

###############################################################################
#                               DATABASE MODELS                               #
###############################################################################

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # "doctor", "patient", "receptionist"
    name = db.Column(db.String(100), nullable=True)
    email = db.Column(db.String(100), nullable=True)

class DoctorAvailability(db.Model):
    __tablename__ = "doctor_availability"
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    day_of_week = db.Column(db.Integer, nullable=False)  # 0-6 for Monday-Sunday
    start_time = db.Column(db.String(20), nullable=False)
    end_time = db.Column(db.String(20), nullable=False)
    is_available = db.Column(db.Boolean, default=True)

    doctor = relationship("User", backref="availability")

class DoctorRate(db.Model):
    __tablename__ = "doctor_rates"
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    rate_per_hour = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    doctor = relationship("User", backref="rate")

class Appointment(db.Model):
    __tablename__ = "appointments"
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    date = db.Column(db.String(20), nullable=False)
    time = db.Column(db.String(20), nullable=False)
    duration = db.Column(db.Integer, default=60)  # Duration in minutes
    status = db.Column(db.String(20), default="Scheduled")
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_paid = db.Column(db.Boolean, default=False)
    payment_date = db.Column(db.DateTime, nullable=True)

    doctor = relationship("User", foreign_keys=[doctor_id], backref="appointments_as_doctor")
    patient = relationship("User", foreign_keys=[patient_id], backref="appointments_as_patient")

    def calculate_cost(self):
        """Calculate the cost of the appointment based on doctor's rate and duration"""
        doctor_rate = DoctorRate.query.filter_by(doctor_id=self.doctor_id).first()
        if not doctor_rate:
            return 0
        
        # Convert duration from minutes to hours, always rounding up to the nearest hour
        hours = (self.duration + 59) // 60  # This will round up to the nearest hour
        return doctor_rate.rate_per_hour * hours

    def is_time_available(self):
        """Check if the appointment time is available for the doctor"""
        # Convert date string to datetime
        appointment_datetime = datetime.strptime(f"{self.date} {self.time}", "%Y-%m-%d %H:%M")
        day_of_week = appointment_datetime.weekday()
        
        # Check doctor's availability for this day
        availability = DoctorAvailability.query.filter_by(
            doctor_id=self.doctor_id,
            day_of_week=day_of_week,
            is_available=True
        ).first()
        
        if not availability:
            return False, "Doctor is not available on this day"
            
        # Check if time is within working hours
        start_time = datetime.strptime(availability.start_time, "%H:%M").time()
        end_time = datetime.strptime(availability.end_time, "%H:%M").time()
        appointment_time = appointment_datetime.time()
        
        if not (start_time <= appointment_time <= end_time):
            return False, "Appointment time is outside working hours"
            
        # Check for existing appointments at the same time
        existing = Appointment.query.filter(
            Appointment.doctor_id == self.doctor_id,
            Appointment.date == self.date,
            Appointment.time == self.time,
            Appointment.status != "Canceled",
            Appointment.id != self.id
        ).first()
        
        if existing:
            return False, "This time slot is already booked"
            
        return True, "Time slot is available"

class Message(db.Model):
    __tablename__ = "messages"
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    # Add relationships
    sender = relationship("User", foreign_keys=[sender_id], backref="sent_messages")
    receiver = relationship("User", foreign_keys=[receiver_id], backref="received_messages")

###############################################################################
#                            UTILITY & INIT SEQUENCE                           #
###############################################################################

# Create all tables (if they don't already exist)
with app.app_context():
    db.create_all()

def hash_password(password):
    return pbkdf2_sha256.hash(password)

def verify_password(password, hashed):
    return pbkdf2_sha256.verify(password, hashed)

def get_current_user():
    """Returns the current logged-in user object from DB, or None if not logged in."""
    if "user_id" in session:
        return User.query.get(session["user_id"])
    return None

###############################################################################
#                                 AUTH ROUTES                                 #
###############################################################################

@app.route("/")
def index():
    """Landing page: if logged in, go to dashboard; else show basic home page."""
    user = get_current_user()
    if user:
        return redirect(url_for("dashboard"))
    return render_template("index.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        role = request.form.get("role")  # "doctor", "patient", "receptionist"
        username = request.form.get("username")
        password = request.form.get("password")
        name = request.form.get("name")
        email = request.form.get("email")

        if User.query.filter_by(username=username).first():
            flash("Username already exists.", "danger")
            return redirect(url_for("signup"))
        
        user = User(
            username=username,
            password=hash_password(password),
            role=role,
            name=name,
            email=email
        )
        db.session.add(user)
        db.session.commit()
        flash("Signup successful. Please log in.", "success")
        return redirect(url_for("login"))
    
    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        user = User.query.filter_by(username=username).first()
        if user and verify_password(password, user.password):
            # Set session
            session["user_id"] = user.id
            flash("Login successful.", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid credentials.", "danger")
            return redirect(url_for("login"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))



###############################################################################
#                                MAIN EXECUTION                               #
###############################################################################

if __name__ == "__main__":
    app.run(debug=True,port=5002)
