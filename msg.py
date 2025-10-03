import keys
from twilio.rest import Client
account_sid = keys.account_sid
auth_token = keys.auth_token
client = Client(account_sid, auth_token)
message = client.messages.create(
    body="Congratulations! You have won the Lucky Draw.",
    from_=keys.twilio_number,
    to=keys.my_number
)
print(message.sid)