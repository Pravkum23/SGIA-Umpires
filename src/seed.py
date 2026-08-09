from datetime import datetime

PEOPLE = [
    ("Selva", 1, 1, "Either"), ("Subbu", 1, 1, "Either"),
    ("Muthu", 1, 1, "Either"), ("Rajkumar", 1, 1, "Either"),
    ("Praba", 1, 1, "Either"), ("Senthil", 1, 1, "Either"),
    ("Mani", 1, 1, "Either"), ("Velu", 1, 0, "Umpire"),
    ("Naga", 1, 1, "Either"), ("Shree", 1, 0, "Umpire"),
    ("Bala", 1, 1, "Either"), ("Hema", 1, 1, "Either"),
    ("Pratheesh", 1, 1, "Either"), ("Malo", 1, 1, "Either"),
    ("Vasu", 1, 1, "Either"), ("Praveen", 0, 1, "Scorer"),
]

RAW_FIXTURES = [
    ("15-Aug-2026 11:00 AM", "Changi Risers", "Black Panthers"),
    ("15-Aug-2026 3:00 PM", "Knights United", "Redbacks"),
    ("15-Aug-2026 7:00 PM", "Spartans", "United Braves"),
    ("16-Aug-2026 7:30 AM", "Seagull UWCC", "Samurai Warriors"),
    ("16-Aug-2026 11:00 AM", "All Stars", "Punggol Paltans"),
    ("16-Aug-2026 3:00 PM", "Centurions", "Glorious"),
    ("16-Aug-2026 6:30 PM", "Waveriders", "Raptors"),
    ("18-Aug-2026 7:00 PM", "Bouncers", "CW Storm"),
    ("19-Aug-2026 7:00 PM", "Karunadu", "Redbacks"),
    ("20-Aug-2026 7:00 PM", "Infinity", "FACC Gabber"),
    ("21-Aug-2026 7:30 PM", "Knights United", "Bouncers"),
    ("22-Aug-2026 11:00 AM", "Samurai Warriors", "Punggol Paltans"),
    ("22-Aug-2026 3:00 PM", "Glorious", "Raptors"),
    ("22-Aug-2026 7:00 PM", "Tamil Titans", "Solid Lanka"),
    ("23-Aug-2026 7:30 AM", "Changi Risers", "Warriors"),
    ("23-Aug-2026 11:00 AM", "Black Panthers", "United Braves"),
    ("23-Aug-2026 3:00 PM", "Bouncers", "Royal Star"),
    ("23-Aug-2026 6:30 PM", "Seagull UWCC", "All Stars"),
    ("25-Aug-2026 7:00 PM", "Samurai Warriors", "FACC Gabber"),
    ("26-Aug-2026 7:00 PM", "SingaLions", "All Stars"),
    ("27-Aug-2026 7:00 PM", "Centurions", "Solid Lanka"),
    ("28-Aug-2026 7:30 PM", "Infinity", "Samurai Warriors"),
    ("29-Aug-2026 11:00 AM", "Knights United", "CW Storm"),
    ("29-Aug-2026 3:00 PM", "SingaLions", "Seagull UWCC"),
    ("29-Aug-2026 7:00 PM", "FACC Gabber", "All Stars"),
    ("30-Aug-2026 7:30 AM", "Starry Knights", "Raptors"),
    ("30-Aug-2026 11:00 AM", "Redbacks", "Royal Star"),
    ("30-Aug-2026 3:00 PM", "Underdogs", "Legends"),
    ("30-Aug-2026 6:30 PM", "Black Panthers", "Spartans"),
]
FIXTURES = [(datetime.strptime(d, "%d-%b-%Y %I:%M %p"), h, a) for d, h, a in RAW_FIXTURES]

