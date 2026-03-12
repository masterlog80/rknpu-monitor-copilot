# app.py

# Your existing imports
import datetime

# Include support for days parameter

def filter_by_timeframe(data, days):
    if days is None:
        return data
    cutoff_date = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    return [entry for entry in data if entry['date'] >= cutoff_date]

# Your existing functions

def main():
    # Your existing code
    pass

if __name__ == '__main__':
    main()