import os
import re
import ssl
from datetime import date
from flask import Flask, render_template, request, redirect, url_for, flash
import pymysql
import pymysql.cursors
from dotenv import load_dotenv

# --- Load environment variables ---
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'super_secret_fallback')

# --- 1. GET BASE DIRECTORY FOR ca.pem ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SSL_CERT_PATH = os.path.join(BASE_DIR, 'ca.pem')


# --- 2. DATABASE CONNECTION + EXECUTION HELPERS ---
# (This section is unchanged and correct)

def get_db_connection():
    """
    Establishes a secure connection to the Aiven MySQL database
    with added diagnostic checks.
    """
    db_host = os.getenv("DB_HOST")
    db_user = os.getenv("DB_USER")
    db_pass = os.getenv("DB_PASS")
    db_name = os.getenv("DB_NAME")
    db_port = os.getenv("DB_PORT")

    if not all([db_host, db_user, db_pass, db_name, db_port]):
        missing = [var for var in ["DB_HOST", "DB_USER", "DB_PASS", "DB_NAME", "DB_PORT"] if not os.getenv(var)]
        raise ValueError(f"❌ STOP! Missing environment variables: {missing}. Please check your .env file.")

    if not os.path.exists(SSL_CERT_PATH):
        raise FileNotFoundError(f"❌ STOP! SSL certificate 'ca.pem' not found at {SSL_CERT_PATH}. Please download it from your Aiven dashboard.")

    try:
        if os.path.getsize(SSL_CERT_PATH) < 100:
             raise ValueError(f"❌ STOP! 'ca.pem' file at {SSL_CERT_PATH} is too small or empty. It's corrupted. Please re-download it.")
        
        with open(SSL_CERT_PATH, 'r') as f:
            content = f.read(50).strip()
            if not content.startswith("-----BEGIN CERTIFICATE-----"):
                raise ValueError(f"❌ STOP! 'ca.pem' file at {SSL_CERT_PATH} does not look like a valid certificate. It might be an HTML or JSON file. Please re-download the raw file.")
    except Exception as e:
        raise type(e)(f"Error reading 'ca.pem' file: {e}")

    ssl_args = { 'ca': SSL_CERT_PATH, 'check_hostname': True }
    
    # print("✅ All checks passed. Attempting connection to Aiven...") # Optional: remove for cleaner logs

    try:
        return pymysql.connect(
            host=db_host, user=db_user, password=db_pass, database=db_name,
            port=int(db_port), ssl=ssl_args, cursorclass=pymysql.cursors.DictCursor
        )
    except pymysql.Error as e:
        print(f"❌ Database Connection Error: {e}")
        if e.args[0] == 2013 or e.args[0] == 2006: 
             print("\n--- HINT ---")
             print("1. Your DB_HOST or DB_PORT in .env is wrong.")
             print("2. A firewall is blocking the connection.")
             print("3. The Aiven service is not running.")
             print("Check your Aiven dashboard connection details carefully.")
             print("------------\n")
        raise e

def execute_query(query, params=None, fetch_one=False, commit=False):
    conn, result = None, None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            if commit:
                conn.commit()
                result = True
            elif fetch_one:
                result = cursor.fetchone()
            else:
                result = cursor.fetchall()
            
    except pymysql.Error as err:
        print(f"Database Error: {err}")
        if conn: conn.rollback()
        raise err 
    except (ValueError, FileNotFoundError) as env_err:
        print(f"Environment Setup Error: {env_err}")
        raise env_err
    
    finally:
        if conn: conn.close()
    return result


# --- 3. INITIALIZE DATABASE STRUCTURE ---
# (This section is unchanged and correct)
@app.cli.command("init-db")
def init_db():
    """Creates necessary tables if they don't exist."""
    queries = [
        """
        CREATE TABLE IF NOT EXISTS events (
            event_id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            event_date DATE NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS participants (
            participant_id INT AUTO_INCREMENT PRIMARY KEY,
            event_id INT,
            name VARCHAR(255),
            contact_info VARCHAR(255),
            registration_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (event_id) REFERENCES events(event_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS winners (
            winner_id INT AUTO_INCREMENT PRIMARY KEY,
            event_id INT,
            participant_id INT,
            prize_name VARCHAR(255),
            draw_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (event_id) REFERENCES events(event_id),
            FOREIGN KEY (participant_id) REFERENCES participants(participant_id)
        )
        """
    ]
    
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            for q in queries:
                cursor.execute(q)
        conn.commit()
        print("✅ Tables verified/created successfully.")
    except Exception as e:
        print(f"❌ Error initializing database: {e}")
    finally:
        if conn:
            conn.close()


# --- 4. ROUTES (WITH ALL FEATURES) ---

@app.route('/')
def index():
    try:
        query = "SELECT event_id, name, event_date FROM events WHERE event_date >= %s ORDER BY event_date ASC LIMIT 3"
        today = date.today()
        upcoming_events = execute_query(query, (today,))
        return render_template('index.html', events=upcoming_events, today=today)
    except Exception as e:
        flash(f"Error loading page: {e}", 'danger') 
        print(f"Error in index route: {e}") 
        return render_template('index.html', events=[], today=date.today())


# --- vvv THIS FUNCTION IS NOW MODIFIED vvv ---
@app.route('/add-event', methods=['GET', 'POST'])
def add_event():
    current_date_obj = date.today()
    current_date_iso = current_date_obj.isoformat()

    if request.method == 'POST':
        try:
            # --- MODIFIED: Delete event logic ---
            if 'delete_event_id' in request.form:
                event_id = request.form['delete_event_id']
                
                # We no longer check for winners. We just delete everything.
                # 1. Delete winner records for this event
                execute_query("DELETE FROM winners WHERE event_id = %s", (event_id,), commit=True)
                
                # 2. Delete participant records for this event
                execute_query("DELETE FROM participants WHERE event_id = %s", (event_id,), commit=True)
                
                # 3. Delete the event itself
                execute_query("DELETE FROM events WHERE event_id = %s", (event_id,), commit=True)
                
                flash('Event (and all its participants and winners) deleted successfully.', 'success')
                # --- END OF MODIFICATION ---

            # Add event logic
            elif 'event_name' in request.form:
                event_name = request.form['event_name']
                event_date = request.form['event_date']
                execute_query("INSERT INTO events (name, event_date) VALUES (%s, %s)", (event_name, event_date), commit=True)
                flash('Event added successfully!', 'success')
                
        except Exception as e:
            flash(f'Error processing request: {e}', 'danger')
            print(f"Error in add_event POST: {e}")
            
        return redirect(url_for('add_event'))

    # GET request
    try:
        existing_events = execute_query("SELECT event_id, name, event_date FROM events ORDER BY event_date DESC")
        return render_template('add-event.html', existing_events=existing_events, today=current_date_obj)
    except Exception as e:
        flash(f"Error loading page: {e}", 'danger')
        print(f"Error in add_event GET: {e}")
        return render_template('add-event.html', existing_events=[], today=current_date_obj)
# --- ^^^ END OF MODIFIED FUNCTION ^^^ ---


@app.route('/add-participant', methods=['GET', 'POST'])
def add_participant():
    if request.method == 'POST':
        # This is for ADDING a new participant
        event_id = request.form['select_event']
        try:
            p_name = request.form['participant_name']
            contact = request.form['contact_info']

            if not re.match(r'^\+91\d{10}$', contact):
                flash('Invalid phone number format (+91XXXXXXXXXX required).', 'danger')
                return redirect(url_for('add_participant', event_id=event_id))

            execute_query("INSERT INTO participants (event_id, name, contact_info) VALUES (%s, %s, %s)",
                            (event_id, p_name, contact), commit=True)
            flash(f'{p_name} added successfully!', 'success')
        except pymysql.Error as e:
            if e.args[0] == 1062: # Duplicate entry
                 flash(f'Error: A participant with this contact info might already be registered.', 'danger')
            else:
                 flash(f'Error adding participant: {e}', 'danger')
        except Exception as e:
            flash(f'Error adding participant: {e}', 'danger')
            print(f"Error in add_participant POST: {e}")
        return redirect(url_for('add_participant', event_id=event_id))

    # GET request (for VIEWING the page and participants)
    try:
        selected_event_id = request.args.get('event_id', type=int)
        current_event = None
        registered_participants = []
        active_events = execute_query("SELECT event_id, name FROM events WHERE event_date >= %s ORDER BY event_date ASC", (date.today(),))

        if selected_event_id:
            current_event = execute_query("SELECT event_id, name FROM events WHERE event_id = %s", (selected_event_id,), fetch_one=True)
            if current_event:
                registered_participants = execute_query("""
                    SELECT participant_id, name, contact_info, registration_time
                    FROM participants WHERE event_id = %s ORDER BY registration_time DESC
                """, (selected_event_id,))
            else:
                 flash("Selected event not found.", 'warning')
                 
        return render_template('add-participant.html',
                           active_events=active_events,
                           current_event=current_event,
                           registered_participants=registered_participants)
                           
    except Exception as e:
        flash(f"Error loading page data: {e}", 'danger')
        print(f"Error in add_participant GET: {e}")
        return render_template('add-participant.html',
                           active_events=[],
                           current_event=None,
                           registered_participants=[])

@app.route('/delete-participant/<int:event_id>/<int:participant_id>', methods=['POST'])
def delete_participant(event_id, participant_id):
    """
    Deletes a participant from an event, even if they have already won.
    This will also remove their associated winner record.
    """
    try:
        # 1. First, delete any winner records for this participant in this event
        execute_query("DELETE FROM winners WHERE participant_id = %s AND event_id = %s", 
                      (participant_id, event_id), commit=True)
        
        # 2. Second, delete the participant themselves
        execute_query("DELETE FROM participants WHERE participant_id = %s AND event_id = %s", 
                      (participant_id, event_id), commit=True)
        
        flash('Participant (and their winner record, if any) deleted successfully.', 'success')
            
    except Exception as e:
        flash(f'Error deleting participant: {e}', 'danger')
        print(f"Error in delete_participant: {e}")
        
    return redirect(url_for('add_participant', event_id=event_id))


@app.route('/draw-winner', methods=['GET', 'POST'])
def draw_winner():
    winner_result = None
    prize_name = None 
    
    if request.method == 'POST':
        try:
            event_id = request.form['draw_event']
            prize_name = request.form['prize_name'] 

            existing = execute_query("SELECT winner_id FROM winners WHERE event_id = %s AND prize_name = %s", (event_id, prize_name))
            if existing:
                flash(f'Prize "{prize_name}" already drawn for this event.', 'warning')
                return redirect(url_for('draw_winner'))

            winner = execute_query("""
                SELECT p.participant_id, p.name, p.contact_info
                FROM participants p
                LEFT JOIN winners w ON p.participant_id = w.participant_id AND w.event_id = %s
                WHERE p.event_id = %s
                GROUP BY p.participant_id, p.name, p.contact_info
                HAVING COUNT(w.winner_id) = 0
                ORDER BY RAND() LIMIT 1
            """, (event_id, event_id), fetch_one=True)

            if winner:
                execute_query("INSERT INTO winners (event_id, participant_id, prize_name) VALUES (%s, %s, %s)",
                                (event_id, winner['participant_id'], prize_name), commit=True)
                flash(f'🎉 Winner: {winner["name"]} for {prize_name}!', 'success')
                winner_result = winner 
            else:
                flash('No eligible participants left to draw.', 'warning')
        except Exception as e:
            flash(f'Error during draw: {e}', 'danger')
            print(f"Error in draw_winner POST: {e}")
    
    # GET request
    try:
        draw_events = execute_query("SELECT event_id, name FROM events WHERE event_date >= %s ORDER BY event_date ASC", (date.today(),))
        return render_template('draw-winner.html', draw_events=draw_events, winner_result=winner_result, prize_name=prize_name)
    except Exception as e:
        flash(f'Error loading page: {e}', 'danger')
        print(f"Error in draw_winner GET: {e}")
        return render_template('draw-winner.html', draw_events=[], winner_result=winner_result, prize_name=prize_name)


@app.route('/view-winners')
def view_winners():
    try:
        winners = execute_query("""
            SELECT w.prize_name, w.draw_time, e.name AS event_name, p.name AS winner_name, p.contact_info
            FROM winners w
            JOIN events e ON w.event_id = e.event_id
            JOIN participants p ON w.participant_id = p.participant_id
            ORDER BY w.draw_time DESC
        """)
        return render_template('view-winners.html', winners=winners)
    except Exception as e:
        flash(f'Error loading winners: {e}', 'danger')
        print(f"Error in view_winners GET: {e}")
        return render_template('view-winners.html', winners=[])


# --- 5. RUN APP ---
if __name__ == '__main__':
    app.run(debug=True)
