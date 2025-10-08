from flask import Flask, render_template, request, redirect, url_for, flash
import mysql.connector # <<< THE REQUIRED CHANGE: Using the official connector
from datetime import date
import re
from twilio.rest import Client
import os

# --- Twilio Setup (Requires environment variables) ---
try:
    ACCOUNT_SID = os.environ.get("account_sid")
    AUTH_TOKEN = os.environ.get("auth_token")
    TWILIO_NUMBER = os.environ.get("twilio_number")
    TWILIO_CLIENT = Client(ACCOUNT_SID, AUTH_TOKEN)
    TWILIO_ENABLED = True
except (AttributeError) as e:
    print(f"Warning: Twilio setup failed. Environment variables not found/set. Error: {e}")
    TWILIO_ENABLED = False


app = Flask(__name__)

# --- 1. CONFIGURATION (Standard Python DB Config) ---
app.secret_key = 'your_super_secret_key'

DB_CONFIG = {
    'host': os.environ.get("localhost"),
    'user': 'root',
    'password': os.environ.get("my_sql_pass"),
    'database': os.environ.get("db_name"),

}

# --- 2. DATABASE CONNECTION MANAGEMENT ---

def get_db_connection():
    """Establishes a new database connection."""
    return mysql.connector.connect(**DB_CONFIG)

def execute_query(query, params=None, fetch_one=False, commit=False):
    """Handles connection, cursor, query execution, and closing."""
    conn = None
    result = None
    try:
        conn = get_db_connection()
        # Use dictionary=True in the cursor to get DictCursor-like behavior
        cursor = conn.cursor(dictionary=True) 
        
        cursor.execute(query, params)
        
        if commit:
            conn.commit()
            result = True
        elif fetch_one:
            result = cursor.fetchone()
        else:
            result = cursor.fetchall()
            
        cursor.close()
    except mysql.connector.Error as err:
        print(f"Database Error: {err}")
        if conn: conn.rollback()
        raise
    finally:
        if conn and conn.is_connected():
            conn.close()
    return result


# --- 3. HELPER FUNCTIONS ---
# ... (send_winner_sms remains the same) ...


# --- 4. ROUTES (Updated to use the new execute_query function) ---

@app.route('/')
def index():
    """Home Page Route"""
    query = "SELECT event_id, name, event_date FROM events WHERE event_date >= %s ORDER BY event_date ASC LIMIT 3"
    upcoming_events = execute_query(query, (date.today(),))
    
    return render_template('index.html', events=upcoming_events)


@app.route('/add-event', methods=['GET', 'POST'])
def add_event():
    """Route to handle event form submission AND event deletion."""
    current_date = date.today()

    if request.method == 'POST':
        # --- DELETE EVENT LOGIC ---
        if 'delete_event_id' in request.form:
            event_id = request.form['delete_event_id']
            try:
                # 1. Check if the event has any winners
                query_count = "SELECT COUNT(winner_id) AS winner_count FROM winners WHERE event_id = %s"
                winner_count = execute_query(query_count, (event_id,), fetch_one=True)['winner_count']
                
                if winner_count > 0:
                    flash(f'Error: Event cannot be deleted as it already has {winner_count} recorded winners.', 'danger')
                else:
                    # 2. Perform deletion (safely delete participants first, then event)
                    query_del_p = "DELETE FROM participants WHERE event_id = %s"
                    execute_query(query_del_p, (event_id,), commit=True)

                    query_del_e = "DELETE FROM events WHERE event_id = %s"
                    execute_query(query_del_e, (event_id,), commit=True)
                    
                    flash('Event and associated participants deleted successfully!', 'success')
            except Exception as e:
                flash(f'Database Error during event deletion: {e}', 'danger')
            finally:
                return redirect(url_for('add_event'))
        
        # --- ADD EVENT LOGIC ---
        event_name = request.form['event_name']
        event_date = request.form['event_date']
        
        try:
            query = "INSERT INTO events (name, event_date) VALUES (%s, %s)"
            execute_query(query, (event_name, event_date), commit=True)
            flash('Event added successfully!', 'success')
        except Exception as e:
            # Check for unique constraint error specifically if needed, otherwise general flash
            flash(f'Error adding event: {e}', 'danger')
        finally:
            return redirect(url_for('add_event'))

    # For GET request: Fetch all events to display in the table
    query = "SELECT event_id, name, event_date FROM events ORDER BY event_date DESC"
    existing_events = execute_query(query)
    
    return render_template('add-event.html', 
                           existing_events=existing_events, 
                           today=current_date)


@app.route('/delete-participant/<int:participant_id>/<int:event_id>', methods=['POST'])
def delete_participant(participant_id, event_id):
    """Deletes a specific participant."""
    try:
        # Check if participant has won a prize before deleting
        query_count = "SELECT COUNT(winner_id) AS win_count FROM winners WHERE participant_id = %s AND event_id = %s"
        win_count = execute_query(query_count, (participant_id, event_id), fetch_one=True)['win_count']

        if win_count > 0:
            flash('Error: Cannot delete a participant who has already won a prize in this event.', 'danger')
        else:
            query_delete = "DELETE FROM participants WHERE participant_id = %s"
            execute_query(query_delete, (participant_id,), commit=True)
            flash('Participant deleted successfully!', 'info')
    except Exception as e:
        flash(f'Error deleting participant: {e}', 'danger')
    
    # Redirect back to the participant list for the specific event
    return redirect(url_for('add_participant', event_id=event_id))


@app.route('/add-participant', methods=['GET', 'POST'])
def add_participant():
    """Route to handle participant registration AND viewing participants by event."""
    
    # 1. Handle Event Dropdown and Participants List (GET/URL parameter)
    selected_event_id = request.args.get('event_id', type=int)
    current_event = None
    registered_participants = []
    
    # Fetch active events for the dropdown
    query_active = "SELECT event_id, name FROM events WHERE event_date >= %s ORDER BY event_date ASC"
    active_events = execute_query(query_active, (date.today(),))
    
    if selected_event_id:
        # Fetch event details
        query_event = "SELECT event_id, name FROM events WHERE event_id = %s"
        current_event = execute_query(query_event, (selected_event_id,), fetch_one=True)

        # Fetch all participants for the selected event
        query_participants = """
            SELECT participant_id, name, contact_info, registration_time
            FROM participants
            WHERE event_id = %s
            ORDER BY registration_time DESC
        """
        registered_participants = execute_query(query_participants, (selected_event_id,))


    # 2. Handle Form Submission (POST)
    if request.method == 'POST':
        event_id = request.form['select_event']
        p_name = request.form['participant_name']
        contact = request.form['contact_info']
        
        # --- PHONE NUMBER VALIDATION CONSTRAINT (India, +91) ---
        indian_phone_regex = r'^\+91\d{10}$'
        if not re.match(indian_phone_regex, contact):
            flash('Error: Contact number must be a valid Indian phone number starting with +91 (e.g., +919876543210).', 'danger')
            return redirect(url_for('add_participant', event_id=event_id if event_id else ''))
        # --- END VALIDATION ---


        try:
            # Insert new participant
            query_insert = "INSERT INTO participants (event_id, name, contact_info) VALUES (%s, %s, %s)"
            execute_query(query_insert, (event_id, p_name, contact), commit=True)
            flash(f'{p_name} added successfully!', 'success')
        except Exception as e:
            flash(f'Error adding participant: {e}', 'danger')
        finally:
            return redirect(url_for('add_participant', event_id=event_id))

    return render_template('add-participant.html', 
                           active_events=active_events,
                           current_event=current_event,
                           registered_participants=registered_participants)


@app.route('/draw-winner', methods=['GET', 'POST'])
def draw_winner():
    """Route to handle the lucky draw process, ensuring one winner per prize."""
    
    query_events = "SELECT event_id, name FROM events WHERE event_date >= %s ORDER BY event_date ASC"
    draw_events = execute_query(query_events, (date.today(),))
    
    winner_result = None

    if request.method == 'POST':
        event_id = request.form['draw_event']
        prize_name = request.form['prize_name']
        
        # Get Event Name for SMS
        query_event_name = "SELECT name FROM events WHERE event_id = %s"
        event_data = execute_query(query_event_name, (event_id,), fetch_one=True)
        event_name = event_data['name'] if event_data else 'Unknown Event'

        try:
            # 1. Check if this prize name has already been drawn for this event
            query_prize_check = "SELECT winner_id FROM winners WHERE event_id = %s AND prize_name = %s"
            if execute_query(query_prize_check, (event_id, prize_name)):
                flash(f'A winner has already been drawn for the prize: "{prize_name}" in this event.', 'warning')
                return redirect(url_for('draw_winner'))
            
            # 2. Select a random eligible participant (HAVING COUNT(w.winner_id) = 0 enforces ONE PRIZE PER PERSON PER EVENT)
            query_winner = """
                SELECT p.participant_id, p.name, p.contact_info
                FROM participants p
                LEFT JOIN winners w ON p.participant_id = w.participant_id AND w.event_id = %s
                WHERE p.event_id = %s 
                GROUP BY p.participant_id, p.name, p.contact_info
                HAVING COUNT(w.winner_id) = 0 
                ORDER BY RAND() 
                LIMIT 1
            """
            winner = execute_query(query_winner, (event_id, event_id), fetch_one=True)

            if winner:
                # 3. Insert the winner record
                query_insert_winner = "INSERT INTO winners (event_id, participant_id, prize_name) VALUES (%s, %s, %s)"
                execute_query(query_insert_winner, (event_id, winner['participant_id'], prize_name), commit=True)
                
                winner_result = {
                    'name': winner['name'],
                    'contact': winner['contact_info'],
                    'prize': prize_name,
                    'event_id': event_id
                }
                
                # 4. Send SMS Notification
                if send_winner_sms(winner['contact_info'], event_name, prize_name):
                    flash(f'🎉 Winner drawn! SMS sent to {winner["name"]}!', 'success')
                else:
                    flash(f'🎉 Winner drawn! Could not send SMS to {winner["name"]}.', 'warning')
                
            else:
                flash('No eligible participants remaining for this draw, or all participants have won a prize.', 'warning')
        except Exception as e:
            flash(f'Error conducting draw: {e}', 'danger')
            
    return render_template('draw-winner.html', draw_events=draw_events, winner_result=winner_result)


@app.route('/view-winners')
def view_winners():
    """Route to display all past winners"""
    query = """
        SELECT 
            w.prize_name, w.draw_time,
            e.name AS event_name, 
            p.name AS winner_name, 
            p.contact_info
        FROM winners w
        JOIN events e ON w.event_id = e.event_id
        JOIN participants p ON w.participant_id = p.participant_id
        ORDER BY w.draw_time DESC
    """
    winners = execute_query(query)
    
    return render_template('view-winners.html', winners=winners)


if __name__ == '__main__':
    # NOTE: Set your OS environment variables (e.g., in your shell or a .env file)
    # export my_sql_pass='Spcsb@2124'
    # export account_sid='YOUR_TWILIO_SID' 
    # export auth_token='YOUR_TWILIO_TOKEN'
    # export twilio_number='+1234567890' 
    app.run(debug=True)
