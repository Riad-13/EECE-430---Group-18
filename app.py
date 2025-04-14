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
#                               DASHBOARD VIEWS                               #
###############################################################################

@app.route("/dashboard")
def dashboard():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    if user.role == "doctor":
        return render_template("dashboard_doctor.html", user=user)
    elif user.role == "patient":
        return render_template("dashboard_patient.html", user=user)
    elif user.role == "receptionist":
        return render_template("dashboard_receptionist.html", user=user)
    else:
        flash("Unknown role.", "danger")
        return redirect(url_for("logout"))

###############################################################################
#                            APPOINTMENT ROUTES                               #
###############################################################################

@app.route("/appointments", methods=["GET"])
def appointments():
    """List all appointments relevant to the current user."""
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))
    
    # If doctor -> show all for that doctor
    # If patient -> show all for that patient
    # If receptionist -> show all appointments across the board
    if user.role == "doctor":
        my_appointments = Appointment.query.filter_by(doctor_id=user.id).all()
    elif user.role == "patient":
        my_appointments = Appointment.query.filter_by(patient_id=user.id).all()
    else:  # receptionist
        my_appointments = Appointment.query.all()

    return render_template("appointments.html", appointments=my_appointments, user=user)

@app.route("/schedule_appointment", methods=["GET", "POST"])
def schedule_appointment():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))
    
    if user.role not in ["patient", "receptionist"]:
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    doctors = User.query.filter_by(role="doctor").all()
    patients = User.query.filter_by(role="patient").all() if user.role == "receptionist" else None

    if request.method == "POST":
        doctor_id = request.form.get("doctor_id")
        date = request.form.get("date")
        time = request.form.get("time")
        duration = request.form.get("duration", 60)  # Default 60 minutes
        notes = request.form.get("notes", "")

        if not all([doctor_id, date, time]):
            flash("All fields are required.", "danger")
            return redirect(url_for("schedule_appointment"))

        patient_id = user.id if user.role == "patient" else request.form.get("patient_id")
        if not patient_id:
            flash("Patient selection is required.", "danger")
            return redirect(url_for("schedule_appointment"))

        # Create temporary appointment object to check availability
        temp_appointment = Appointment(
            doctor_id=doctor_id,
            patient_id=patient_id,
            date=date,
            time=time,
            duration=int(duration)
        )
        
        is_available, message = temp_appointment.is_time_available()
        if not is_available:
            flash(message, "danger")
            return redirect(url_for("schedule_appointment"))

        appointment = Appointment(
            doctor_id=doctor_id,
            patient_id=patient_id,
            date=date,
            time=time,
            duration=int(duration),
            notes=notes,
            status="Scheduled"
        )
        db.session.add(appointment)
        db.session.commit()
        flash("Appointment scheduled successfully!", "success")
        return redirect(url_for("appointments"))

    return render_template("schedule_appointment.html", doctors=doctors, patients=patients, user=user)

@app.route("/cancel_appointment/<int:appointment_id>", methods=["POST"])
def cancel_appointment(appointment_id):
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    appointment = Appointment.query.get_or_404(appointment_id)

    # Check if user is authorized to cancel 
    # (patient who booked it, the doctor, or the receptionist)
    if user.role == "patient" and appointment.patient_id != user.id:
        flash("Unauthorized to cancel this appointment.", "danger")
        return redirect(url_for("appointments"))
    # Doctors can also cancel their appointments
    if user.role == "doctor" and appointment.doctor_id != user.id:
        flash("Unauthorized to cancel this appointment.", "danger")
        return redirect(url_for("appointments"))
    # Receptionist can cancel any appointment

    appointment.status = "Canceled"
    db.session.commit()
    flash("Appointment canceled.", "info")
    return redirect(url_for("appointments"))

@app.route("/reschedule_appointment/<int:appointment_id>", methods=["GET", "POST"])
def reschedule_appointment(appointment_id):
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))

    appointment = Appointment.query.get_or_404(appointment_id)

    # Check if user is authorized to reschedule 
    if user.role == "patient" and appointment.patient_id != user.id:
        flash("Unauthorized to reschedule this appointment.", "danger")
        return redirect(url_for("appointments"))
    if user.role == "doctor" and appointment.doctor_id != user.id:
        flash("Unauthorized to reschedule this appointment.", "danger")
        return redirect(url_for("appointments"))

    if request.method == "POST":
        new_date = request.form.get("date")
        new_time = request.form.get("time")
        duration = request.form.get("duration", appointment.duration)
        notes = request.form.get("notes", "")

        # Create temporary appointment object to check availability
        temp_appointment = Appointment(
            doctor_id=appointment.doctor_id,
            patient_id=appointment.patient_id,
            date=new_date,
            time=new_time,
            duration=int(duration)
        )
        
        is_available, message = temp_appointment.is_time_available()
        if not is_available:
            flash(message, "danger")
            return redirect(url_for("reschedule_appointment", appointment_id=appointment_id))

        appointment.date = new_date
        appointment.time = new_time
        appointment.duration = int(duration)
        appointment.notes = notes
        appointment.status = "Rescheduled"
        db.session.commit()
        flash("Appointment rescheduled successfully.", "success")
        return redirect(url_for("appointments"))

    doctors = [User.query.get(appointment.doctor_id)]
    patients = [User.query.get(appointment.patient_id)] if user.role == "receptionist" else None
    
    return render_template("schedule_appointment.html", 
                         appointment=appointment, 
                         user=user, 
                         doctors=doctors,
                         patients=patients)

@app.route("/manage_availability", methods=["GET", "POST"])
def manage_availability():
    user = get_current_user()
    if not user or user.role != "doctor":
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        day = request.form.get("day")
        start_time = request.form.get("start_time")
        end_time = request.form.get("end_time")
        is_available = request.form.get("is_available") == "true"

        availability = DoctorAvailability.query.filter_by(
            doctor_id=user.id,
            day_of_week=day
        ).first()

        if availability:
            availability.start_time = start_time
            availability.end_time = end_time
            availability.is_available = is_available
        else:
            availability = DoctorAvailability(
                doctor_id=user.id,
                day_of_week=day,
                start_time=start_time,
                end_time=end_time,
                is_available=is_available
            )
            db.session.add(availability)

        db.session.commit()
        flash("Availability updated successfully.", "success")
        return redirect(url_for("manage_availability"))

    availability = DoctorAvailability.query.filter_by(doctor_id=user.id).all()
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    
    return render_template("manage_availability.html", 
                         availability=availability,
                         days=days,
                         user=user)

@app.route("/manage_rate", methods=["GET", "POST"])
def manage_rate():
    user = get_current_user()
    if not user or user.role != "doctor":
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        rate_per_hour = request.form.get("rate_per_hour")
        
        if not rate_per_hour or float(rate_per_hour) <= 0:
            flash("Please enter a valid rate.", "danger")
            return redirect(url_for("manage_rate"))
            
        doctor_rate = DoctorRate.query.filter_by(doctor_id=user.id).first()
        
        if doctor_rate:
            doctor_rate.rate_per_hour = float(rate_per_hour)
        else:
            doctor_rate = DoctorRate(
                doctor_id=user.id,
                rate_per_hour=float(rate_per_hour)
            )
            db.session.add(doctor_rate)
            
        db.session.commit()
        flash("Rate updated successfully.", "success")
        return redirect(url_for("manage_rate"))
        
    doctor_rate = DoctorRate.query.filter_by(doctor_id=user.id).first()
    return render_template("manage_rate.html", user=user, doctor_rate=doctor_rate)

@app.route("/billing", methods=["GET"])
def billing():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))
        
    if user.role == "patient":
        appointments = Appointment.query.filter_by(patient_id=user.id).all()
    elif user.role == "doctor":
        appointments = Appointment.query.filter_by(doctor_id=user.id).all()
    else:  # receptionist
        appointments = Appointment.query.all()
        
    return render_template("billing.html", appointments=appointments, user=user)

@app.route("/mark_paid/<int:appointment_id>", methods=["POST"])
def mark_paid(appointment_id):
    user = get_current_user()
    if not user or user.role not in ["doctor", "receptionist"]:
        flash("Access denied.", "danger")
        return redirect(url_for("dashboard"))
        
    appointment = Appointment.query.get_or_404(appointment_id)
    
    if user.role == "doctor" and appointment.doctor_id != user.id:
        flash("Unauthorized to mark this appointment as paid.", "danger")
        return redirect(url_for("billing"))
        
    appointment.is_paid = True
    appointment.payment_date = datetime.utcnow()
    db.session.commit()
    
    flash("Payment recorded successfully.", "success")
    return redirect(url_for("billing"))

###############################################################################
#                                MAIN EXECUTION                               #
###############################################################################

if __name__ == "__main__":
    app.run(debug=True,port=5002)
