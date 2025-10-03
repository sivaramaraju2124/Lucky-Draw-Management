from flask import Flask, render_template, request, redirect, url_for, flash
from flask_mysqldb import MySQL
from datetime import date
import re # Import regex for validation
from twilio.rest import Client # Import Twilio client

# --- Twilio Setup (Requires 'keys.py' in the same directory) ---
try:
    import keys
    ACCOUNT_SID = keys.account_sid
    AUTH_TOKEN = keys.auth_token
    TWILIO_NUMBER = keys.twilio_number
    TWILIO_CLIENT = Client(ACCOUNT_SID, AUTH_TOKEN)
    TWILIO_ENABLED = True
except (ImportError, AttributeError) as e:
    print(f"Warning: Twilio setup failed. Check keys.py. Error: {e}")
    TWILIO_ENABLED = False
    


app = Flask(__name__)

# --- 1. CONFIGURATION ---
app.secret_key = 'your_super_secret_key'

# MySQL Configuration
app.config['MYSQL_HOST'] = 'localhost'
app.config['MYSQL_USER'] = 'root'
app.config['MYSQL_PASSWORD'] = keys.my_sql_pass
app.config['MYSQL_DB'] = 'lucky_draw_db'
app.config['MYSQL_CURSORCLASS'] = 'DictCursor'

mysql = MySQL(app)

# --- 2. HELPER FUNCTIONS ---

def send_winner_sms(winner_contact, event_name, prize_name):
    """Sends an SMS to the winner using Twilio."""
    if not TWILIO_ENABLED:
        print("SMS not sent: Twilio is disabled.")
        return False
        
    try:
        message_body = (
            f"🎉 Congratulations! You have won the {prize_name} "
            f"in the {event_name} Lucky Draw! Contact admin to claim your prize."
        )
        
        message = TWILIO_CLIENT.messages.create(
            body=message_body,
            from_=TWILIO_NUMBER,
            to=winner_contact
        )
        print(f"SMS Sent: {message.sid}")
        return True
    except Exception as e:
        print(f"Twilio SMS Error: {e}")
        return False

# --- 3. ROUTES ---

@app.route('/')
def index():
    """Home Page Route"""
    cur = mysql.connection.cursor()
    cur.execute("SELECT event_id, name, event_date FROM events WHERE event_date >= %s ORDER BY event_date ASC LIMIT 3", (date.today(),))
    upcoming_events = cur.fetchall()
    cur.close()
    return render_template('index.html', events=upcoming_events)


@app.route('/add-event', methods=['GET', 'POST'])
def add_event():
    """Route to handle event form submission AND event deletion."""
    current_date = date.today()

    if request.method == 'POST':
        # --- DELETE EVENT LOGIC (CORRECTED) ---
        if 'delete_event_id' in request.form:
            event_id = request.form['delete_event_id']
            cur = mysql.connection.cursor()
            try:
                # 1. Check if the event has any winners (the absolute safety constraint)
                cur.execute("SELECT COUNT(winner_id) AS winner_count FROM winners WHERE event_id = %s", (event_id,))
                winner_count = cur.fetchone()['winner_count']
                
                if winner_count > 0:
                    # Deletion fails if a winner is recorded
                    flash(f'Error: Event cannot be deleted as it already has {winner_count} recorded winners.', 'danger')
                else:
                    # Deletion succeeds if no winners, regardless of event date
                    cur.execute("DELETE FROM participants WHERE event_id = %s", (event_id,))
                    cur.execute("DELETE FROM events WHERE event_id = %s", (event_id,))
                    mysql.connection.commit()
                    flash('Event and associated participants deleted successfully!', 'success')
            except Exception as e:
                flash(f'Database Error during event deletion: {e}', 'danger')
            finally:
                cur.close()
                return redirect(url_for('add_event'))
        
        # --- ADD EVENT LOGIC ---
        event_name = request.form['event_name']
        event_date = request.form['event_date']
        
        cur = mysql.connection.cursor()
        try:
            cur.execute("INSERT INTO events (name, event_date) VALUES (%s, %s)", (event_name, event_date))
            mysql.connection.commit()
            flash('Event added successfully!', 'success')
        except Exception as e:
            flash(f'Error adding event (name unique constraint?): {e}', 'danger')
        finally:
            cur.close()
            return redirect(url_for('add_event'))

    # For GET request: Fetch all events to display in the table
    cur = mysql.connection.cursor()
    cur.execute("SELECT event_id, name, event_date FROM events ORDER BY event_date DESC")
    existing_events = cur.fetchall()
    cur.close()
    
    return render_template('add-event.html', 
                           existing_events=existing_events, 
                           today=current_date)


@app.route('/delete-participant/<int:participant_id>/<int:event_id>', methods=['POST'])
def delete_participant(participant_id, event_id):
    """Deletes a specific participant."""
    cur = mysql.connection.cursor()
    try:
        # Check if participant has won a prize before deleting (optional extra safety)
        cur.execute("SELECT COUNT(winner_id) AS win_count FROM winners WHERE participant_id = %s AND event_id = %s", (participant_id, event_id))
        if cur.fetchone()['win_count'] > 0:
            flash('Error: Cannot delete a participant who has already won a prize in this event.', 'danger')
        else:
            cur.execute("DELETE FROM participants WHERE participant_id = %s", (participant_id,))
            mysql.connection.commit()
            flash('Participant deleted successfully!', 'info')
    except Exception as e:
        flash(f'Error deleting participant: {e}', 'danger')
    finally:
        cur.close()
    
    # Redirect back to the participant list for the specific event
    return redirect(url_for('add_participant', event_id=event_id))


@app.route('/add-participant', methods=['GET', 'POST'])
def add_participant():
    """Route to handle participant registration AND viewing participants by event."""
    cur = mysql.connection.cursor()
    
    # 1. Handle Event Dropdown and Participants List (GET/URL parameter)
    selected_event_id = request.args.get('event_id', type=int)
    current_event = None
    registered_participants = []
    
    # Fetch active events for the dropdown
    cur.execute("SELECT event_id, name FROM events WHERE event_date >= %s ORDER BY event_date ASC", (date.today(),))
    active_events = cur.fetchall()
    
    if selected_event_id:
        # Fetch event details
        cur.execute("SELECT event_id, name FROM events WHERE event_id = %s", (selected_event_id,))
        current_event = cur.fetchone()

        # Fetch all participants for the selected event
        cur.execute("""
            SELECT participant_id, name, contact_info, registration_time
            FROM participants
            WHERE event_id = %s
            ORDER BY registration_time DESC
        """, (selected_event_id,))
        registered_participants = cur.fetchall()


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
            cur.execute("INSERT INTO participants (event_id, name, contact_info) VALUES (%s, %s, %s)", 
                        (event_id, p_name, contact))
            mysql.connection.commit()
            flash(f'{p_name} added successfully!', 'success')
        except Exception as e:
            flash(f'Error adding participant: {e}', 'danger')
        finally:
            cur.close()
            # Redirect, keeping the selected event ID in the URL to refresh the list
            return redirect(url_for('add_participant', event_id=event_id))

    cur.close()
    return render_template('add-participant.html', 
                           active_events=active_events,
                           current_event=current_event,
                           registered_participants=registered_participants)


@app.route('/draw-winner', methods=['GET', 'POST'])
def draw_winner():
    # ... (draw_winner logic remains the same as it was already correct)
    cur = mysql.connection.cursor()
    
    cur.execute("SELECT event_id, name FROM events WHERE event_date >= %s ORDER BY event_date ASC", (date.today(),))
    draw_events = cur.fetchall()
    
    winner_result = None

    if request.method == 'POST':
        event_id = request.form['draw_event']
        prize_name = request.form['prize_name']
        
        # Get Event Name for SMS
        cur.execute("SELECT name FROM events WHERE event_id = %s", (event_id,))
        event_name = cur.fetchone()['name'] if cur.rowcount else 'Unknown Event'

        try:
            # 1. Check if this prize name has already been drawn for this event
            cur.execute("SELECT winner_id FROM winners WHERE event_id = %s AND prize_name = %s", (event_id, prize_name))
            if cur.rowcount > 0:
                flash(f'A winner has already been drawn for the prize: "{prize_name}" in this event.', 'warning')
                return redirect(url_for('draw_winner'))
            
            # 2. Select a random eligible participant (HAVING COUNT(w.winner_id) = 0 enforces ONE PRIZE PER PERSON PER EVENT)
            cur.execute("""
                SELECT p.participant_id, p.name, p.contact_info
                FROM participants p
                LEFT JOIN winners w ON p.participant_id = w.participant_id AND w.event_id = %s
                WHERE p.event_id = %s 
                GROUP BY p.participant_id, p.name, p.contact_info
                HAVING COUNT(w.winner_id) = 0 
                ORDER BY RAND() 
                LIMIT 1
            """, (event_id, event_id))
            
            winner = cur.fetchone()

            if winner:
                # 3. Insert the winner record
                cur.execute("INSERT INTO winners (event_id, participant_id, prize_name) VALUES (%s, %s, %s)", 
                            (event_id, winner['participant_id'], prize_name))
                mysql.connection.commit()
                
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
        finally:
            cur.close()
            
    return render_template('draw-winner.html', draw_events=draw_events, winner_result=winner_result)


@app.route('/view-winners')
def view_winners():
    # ... (view_winners route remains the same) ...
    cur = mysql.connection.cursor()
    
    # Select all winner details using JOINs
    cur.execute("""
        SELECT 
            w.prize_name, w.draw_time,
            e.name AS event_name, 
            p.name AS winner_name, 
            p.contact_info
        FROM winners w
        JOIN events e ON w.event_id = e.event_id
        JOIN participants p ON w.participant_id = p.participant_id
        ORDER BY w.draw_time DESC
    """)
    
    winners = cur.fetchall()
    cur.close()
    return render_template('view-winners.html', winners=winners)


if __name__ == '__main__':
    app.run(debug=True)