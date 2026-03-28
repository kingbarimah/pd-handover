#!/usr/bin/env python3
import os, sys, requests, re, json, time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# Configuration — loaded from environment variables
# Set these before running:
#   export PD_API_KEY=your-api-key
#   export PD_MY_NAME="Your Name"
#   export PD_NOTES_FOLDER=/path/to/handovers
#   export PD_META_FOLDER=/path/to/metadata
# ---------------------------------------------------------------------------
full_name       = os.environ.get("PD_MY_NAME", "")
notes_folder    = os.environ.get("PD_NOTES_FOLDER", "./handovers")
metadata_folder = os.environ.get("PD_META_FOLDER", "./.handover_metadata")
pd_url          = "https://api.pagerduty.com"

# Auth functions
def check_api_key():
    api_key = os.getenv('PD_API_KEY')
    if not api_key:
        sys.exit("PagerDuty API key needs to be set.\nRun: export PD_API_KEY='your-api-key'")
    if not full_name:
        sys.exit("Your name needs to be set.\nRun: export PD_MY_NAME='Your Name'")
    return api_key

def auth_session(api_key):
    session = requests.Session()
    session.headers.update({
        'Authorization': f'Token token={api_key}',
        'Accept': 'application/vnd.pagerduty+json;version=2'
    })
    return session

def pd_request(session, endpoint):
    r = session.get(f"{pd_url}{endpoint}")
    return r.json() if r.ok else {}

# Remove words we don't want
def clean_alerts(text):
    if not text:
        return text
    text = re.sub(r'SA Alerting - \[FIRING:\d+\]\s*|\[FIRING:\d+\]\s*', '', text)
    return re.sub(r'\b(CRITICAL|WARNING|UNKNOWN|CRIT|WARN)\b[\s:-]*', '', text, flags=re.I).strip()

# For multiple runs during PD shift - additional checks for existing files
def load_existing_incident_ids(handover_file, date_str):
    lines = open(handover_file).readlines() if os.path.exists(handover_file) else []
    os.makedirs(metadata_folder, exist_ok=True)
    meta_file = os.path.join(metadata_folder, f"incidents_{date_str}.json")
    ids = set()

    if os.path.exists(meta_file):
        if not os.path.exists(handover_file):
            print("\n Deleting orphaned metadata file...\n")
            os.remove(meta_file)
        else:
            try:
                ids = set(json.load(open(meta_file)))
                print(f"Loaded {len(ids)} existing incident IDs")
            except json.JSONDecodeError:
                print("Warning: Could not parse metadata, starting fresh")

    return lines, ids, meta_file

# Add incident ids to metadata file - helps prevent duplicates
def save_incident_ids(meta_file, ids):
    with open(meta_file, 'w') as f:
        json.dump(list(ids), f, indent=2)

def format_lines(incident, error_msg, description):
    title = clean_alerts(incident['title'])
    service = incident.get('service', {}).get('summary', 'Unknown Service')
    error_msg = clean_alerts(error_msg)

    # Prometheus SA Alerting
    if "SA Alerting" in service:
        dc_match = re.search(r'ATS \(([^)]+)\)', service)
        datacenter = f"ATS ({dc_match.group(1)})" if dc_match else ""
        metric = title.split()[0] if title else "Unknown"
        content = description if description else title
        return f"• {datacenter + ' - ' if datacenter else ''}{metric} - {content} - [RESOLUTION]"

    if " on " in title:
        parts = title.split(" on ", 1)
        hostname = parts[1].split()[0]
        detail = f": {error_msg}" if error_msg else ""
        return f"• {hostname} - {parts[0]}{detail} - [RESOLUTION]"

    # Default format
    detail = f": {error_msg}" if error_msg else ""
    return f"• {title}{detail} - [RESOLUTION]"

# Helps with pagination issues
def grab_all_incidents(session, since, max_incidents=1000):
    print("Grabbing incidents from PagerDuty...")
    all_incidents = []
    offset, limit = 0, 100

    while True:
        resp = pd_request(session, f"/incidents?since={since}&limit={limit}&offset={offset}")
        batch = resp.get('incidents', [])
        all_incidents.extend(batch)
        print(f"  Found {len(batch)} incidents (total: {len(all_incidents)})")

        if not resp.get('more', False) or offset >= max_incidents:
            break
        offset += limit

    print(f"Total incidents retrieved: {len(all_incidents)}\n")
    return all_incidents

def get_incident_details(incident_id, session):
    time.sleep(0.05)
    logs = pd_request(session, f"/incidents/{incident_id}/log_entries")
    acked = any(
        e.get('agent', {}).get('summary') == full_name and e.get('type') == 'acknowledge_log_entry'
        for e in logs.get('log_entries', [])
    )
    error_msg = description = ""
    if acked:
        alerts = pd_request(session, f"/incidents/{incident_id}/alerts").get('alerts', [])
        if alerts:
            details = alerts[0].get('body', {}).get('details', {})
            error_msg = details.get('SERVICEOUTPUT', '')
            firing_text = details.get('firing', '')
            if firing_text:
                m = re.search(r'Annotations:.*?- description = (.+?)(?:\n|$)', firing_text, re.S)
                if m:
                    description = m.group(1).strip()
    return acked, error_msg, description

def incident_cleanup(all_incidents, session, existing_ids, handover_lines):
    new_lines = []

    def process_one(inc):
        iid = inc['id']
        if iid in existing_ids:
            return
        acked, err, desc = get_incident_details(iid, session)
        if not acked:
            return
        line = format_lines(inc, err, desc)
        if line in handover_lines:
            return
        existing_ids.add(iid)
        return line

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(process_one, inc) for inc in all_incidents]
        for f in as_completed(futures):
            if (line := f.result()):
                new_lines.append(line)
                print(f"  [New] {line[:80]}")

    return new_lines

# Default to 8 hours if hours_back is not passed
def main():
    hours_back = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    api_key = check_api_key()
    since = (datetime.utcnow() - timedelta(hours=hours_back)).strftime('%Y-%m-%dT%H:%M:%SZ')
    print(f"Looking for incidents from the last {hours_back} hours...\n")

    today = datetime.now().strftime('%Y_%m_%d')
    output_file = f"{notes_folder}/handover_{today}.txt"
    os.makedirs(notes_folder, exist_ok=True)

    existing_lines, existing_ids, meta_file = load_existing_incident_ids(output_file, today)

    session = auth_session(api_key)
    all_incidents = grab_all_incidents(session, since)

    handover_lines = [line.rstrip('\n') for line in existing_lines] if existing_lines else ["Handover:"]
    new_lines = incident_cleanup(all_incidents, session, existing_ids, handover_lines)
    handover_lines.extend(new_lines)

    with open(output_file, 'w') as f:
        f.write('\n'.join(handover_lines))
    save_incident_ids(meta_file, existing_ids)

    total = sum(l.strip().startswith('•') for l in handover_lines)
    print(f"\n{'='*60}\nHandover Report Summary\n{'='*60}\n"
          f"Output file: {output_file}\nNew items added: {len(new_lines)}\nTotal items: {total}\n{'='*60}")

if __name__ == "__main__":
    main()