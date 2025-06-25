import os
import io
import base64
import uuid
from datetime import datetime
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import qrcode
import yagmail
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()  # Load .env variables

app = Flask(__name__)
CORS(app)

# PostgreSQL config
DB_CONFIG = {
    'host': os.getenv('POSTGRES_HOST', 'localhost'),
    'user': os.getenv('POSTGRES_USER', 'postgres'),
    'password': os.getenv('POSTGRES_PASSWORD', ''),
    'dbname': os.getenv('POSTGRES_DB', 'wedding_db')
}

ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'supersecret')


def get_db_connection():
    try:
        return psycopg2.connect(**DB_CONFIG)
    except psycopg2.Error as err:
        print(f"DB Error: {err}")
        return None


def init_db():
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS guests (
                id VARCHAR(255) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                email VARCHAR(255) NOT NULL UNIQUE,
                qr_code_data VARCHAR(255),
                checked_in BOOLEAN DEFAULT FALSE,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        cursor.close()
        conn.close()


@app.route('/')
def home():
    return 'Flask backend is running with PostgreSQL!'


def generate_qr_code_image(data):
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    name = data.get('name')
    email = data.get('email')
    if not name or not email:
        return jsonify({'error': 'Name and email required'}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection error'}), 500

    cursor = conn.cursor()
    try:
        # First check if email already exists
        cursor.execute("SELECT id FROM guests WHERE email = %s", (email,))
        if cursor.fetchone():
            return jsonify({'error': 'This email is already registered'}), 400

        guest_id = str(uuid.uuid4())
        
        try:
            cursor.execute("""
                INSERT INTO guests (id, name, email, qr_code_data, checked_in)
                VALUES (%s, %s, %s, %s, %s)
            """, (guest_id, name, email, guest_id, False))
            conn.commit()
        except psycopg2.IntegrityError as e:
            conn.rollback()
            if "duplicate key" in str(e).lower() and "email" in str(e).lower():
                return jsonify({'error': 'This email is already registered'}), 400
            raise

        # Generate QR code
        print("Generating QR code for:", guest_id)
        qr_buf = generate_qr_code_image(guest_id)
        qr_bytes = qr_buf.getvalue()
        qr_base64 = base64.b64encode(qr_bytes).decode()

        # Save QR to a temporary file
        qr_filename = f"{guest_id}.png"
        with open(qr_filename, 'wb') as f:
            f.write(qr_bytes)

        # Send email with QR code
        try:
            yag = yagmail.SMTP(user=os.getenv('EMAIL_USER'), password=os.getenv('EMAIL_PASS'))
            yag.send(
                to=email,
                subject="Your wedding registration QR code",
                contents=[
                    f"Dear {name},\n\nThank you for registering for the wedding. Please find your QR code attached. Show this at the entrance to check in.\n\nBest regards,\nWedding Team"
                ],
                attachments=[qr_filename]
            )
            print(f"QR code sent to {email}")
        except Exception as e:
            print("Email error: ", str(e))
            # Clean up the registration since email failed
            cursor.execute("DELETE FROM guests WHERE id = %s", (guest_id,))
            conn.commit()
            return jsonify({'error': 'Failed to send email with QR code'}), 500
        finally:
            if os.path.exists(qr_filename):
                os.remove(qr_filename)

        return jsonify({
            'message': 'Registration successful',
            'guest_id': guest_id,
            'qr_code_image': f'data:image/png;base64,{qr_base64}'
        }), 201
    
    except Exception as e:
        print("Registration error:", str(e))
        return jsonify({'error': 'Registration failed'}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/checkin', methods=['POST'])
def checkin():
    data = request.get_json()
    guest_id = data.get('guest_id')
    if not guest_id:
        return jsonify({'error': 'Guest ID required'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM guests WHERE id = %s", (guest_id,))
        if not cursor.fetchone():
            return jsonify({'error': 'Guest not found'}), 404

        cursor.execute("UPDATE guests SET checked_in = TRUE WHERE id = %s", (guest_id,))
        conn.commit()
        return jsonify({'message': f'{guest_id} checked in'}), 200
    finally:
        cursor.close()
        conn.close()


@app.route('/guests', methods=['GET'])
def guests():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cursor.execute("SELECT * FROM guests")
    guests = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(guests), 200


@app.route('/admin-login', methods=['POST'])
def admin_login():
    data = request.get_json()
    if data.get('password') == ADMIN_PASSWORD:
        return jsonify({'status': 'success'}), 200
    return jsonify({'error': 'Invalid password'}), 401


if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
