#!/usr/bin/env python3
"""
Timetable Analyzer Web UI - Slot-Based Approach
"""

from flask import Flask, render_template, request, jsonify, Response, session, redirect, url_for
import os
import re
import json
import secrets
import uuid as uuid_module
import socket
from datetime import datetime, timedelta
from timetable_analyzer import TimetableAnalyzer, TimetableConstraints
from collections import defaultdict
from config import TIMETABLE_FILENAME, SQLALCHEMY_DATABASE_URI, SQLALCHEMY_TRACK_MODIFICATIONS
from models import db, User, UserCourse
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
import requests
import google.oauth2.credentials
import google_auth_oauthlib.flow
from googleapiclient.discovery import build

# Force IPv4 to prevent httplib2 from hanging on macOS due to blackholed IPv6
old_getaddrinfo = socket.getaddrinfo
def new_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if family == 0: # AF_UNSPEC
        family = socket.AF_INET
    return old_getaddrinfo(host, port, family, type, proto, flags)
socket.getaddrinfo = new_getaddrinfo

# Set a global socket timeout to prevent Google API from hanging
socket.setdefaulttimeout(15)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'default-dev-secret-key-change-in-prod')

app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = SQLALCHEMY_TRACK_MODIFICATIONS
app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

with app.app_context():
    db.create_all()

# Allow OAuth over HTTP for local testing if not on Render
if os.environ.get('RENDER'):
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
else:
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

if os.path.exists('/etc/secrets/credentials.json'):
    CLIENT_SECRETS_FILE = '/etc/secrets/credentials.json'
else:
    CLIENT_SECRETS_FILE = 'credentials.json'
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'openid',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile'
]

# Calendar subscription storage
CALENDARS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'calendars')
os.makedirs(CALENDARS_DIR, exist_ok=True)

# Load analyzer globally
XLSX_PATH = TIMETABLE_FILENAME
analyzer = None

def get_analyzer():
    global analyzer
    if analyzer is None:
        analyzer = TimetableAnalyzer(XLSX_PATH)
    return analyzer


def get_timetable_version():
    basename = os.path.basename(XLSX_PATH)
    match = re.search(r'(FSC)_(F|S)(\d{2})_TT_(v[\d\.]+)', basename)
    if match:
        campus = match.group(1)
        term = "Fall" if match.group(2) == 'F' else "Spring"
        year = "20" + match.group(3)
        version = match.group(4)
        return f"{campus} {term} {year} {version}"
    # Fallback to the raw filename without extension if pattern doesn't match
    return os.path.splitext(basename)[0]

@app.route('/')
def index():
    return render_template('index.html', version_info=get_timetable_version())


@app.route('/api/courses/<batch>')
def get_courses(batch):
    """Get ALL CS courses (for repeaters who may need courses from other semesters)."""
    a = get_analyzer()
    courses = a.get_all_cs_courses()
    return jsonify(courses)


@app.route('/api/courses-with-sections/<batch>')
def get_courses_with_sections(batch):
    """Get ALL CS courses with their available sections."""
    a = get_analyzer()
    
    # Group courses by name with their sections
    courses_with_sections = {}
    
    for course in a.courses:
        if not course.section.startswith("BCS-"):
            continue
        
        name = course.short_title
        # Skip empty course names
        if not name or not name.strip():
            continue
            
        if name not in courses_with_sections:
            courses_with_sections[name] = {
                'name': name,
                'category': course.category,
                'credit_hours': course.credit_hours,
                'is_lab': course.is_lab(),
                'sections': []
            }
        
        # Get time slots for this section
        slots = [{'day': d, 'time': t, 'venue': v} for d, t, v in course.get_time_slots()]
        
        courses_with_sections[name]['sections'].append({
            'section': course.section,
            'instructor': course.instructor,
            'instructor_short': course.instructor_short,
            'slots': slots
        })
    
    return jsonify(courses_with_sections)


@app.route('/api/instructors/<batch>')
def get_instructors(batch):
    """Get ALL CS instructors."""
    a = get_analyzer()
    instructors = a.get_all_cs_instructors()
    return jsonify(instructors)


def get_slot_key(day, time):
    """Create a consistent key for a time slot."""
    time_start = time.split('-')[0] if '-' in time else time
    return f"{day}_{time_start}"


def get_slot_pattern(courses):
    """Get the slot pattern (set of occupied time slots) for a list of courses."""
    slots = set()
    for course in courses:
        for day, time, venue in course.get_time_slots():
            slots.add(get_slot_key(day, time))
    return frozenset(slots)


@app.route('/api/generate', methods=['POST'])
def generate_timetable():
    """Generate SLOT-BASED timetable options.
    
    Returns slot patterns where:
    - Each pattern is a unique combination of occupied time slots
    - For each slot, we show all courses/sections that can fit there
    """
    data = request.json
    
    # Build wildcard counts
    wildcard_counts = {}
    if data.get('cs_electives', 0) > 0:
        wildcard_counts["CS Elective"] = data['cs_electives']
    if data.get('university_electives', 0) > 0:
        wildcard_counts["University Elective"] = data['university_electives']
    if data.get('robo_electives', 0) > 0:
        wildcard_counts["Robo Elective"] = data['robo_electives']
    
    # Parse courses with section preferences
    # New format: { courseName: { selectedSections: ['BCS-4A', 'BCS-6A'], ... } }
    # Legacy format: [courseName1, courseName2, ...]
    courses_data = data.get('courses', {})
    
    if isinstance(courses_data, list):
        # Legacy format - treat as "any" section
        required_courses = courses_data
        section_preferences = {}
    else:
        # New format with section preferences (array of allowed sections)
        required_courses = list(courses_data.keys())
        section_preferences = {}
        for name, info in courses_data.items():
            if 'selectedSections' in info:
                # New multi-select format
                section_preferences[name] = info['selectedSections']
            elif 'section' in info:
                # Old single-select format (backward compatibility)
                section_preferences[name] = info['section']
            else:
                section_preferences[name] = 'any'
    
    constraints = TimetableConstraints(
        batch=data.get('batch', 'BCS-2022'),
        required_courses=required_courses,
        excluded_instructors=data.get('excluded_instructors', []),
        excluded_courses=data.get('excluded_courses', []),
        excluded_time_slots=data.get('excluded_slots', []),
        wildcard_counts=wildcard_counts,
        section_preferences=section_preferences,
        only_repeater_sections=data.get('only_repeater', False),
    )
    
    a = get_analyzer()
    
    # Generate timetables to find diverse slot patterns
    # Limited to 200 for faster response on free-tier servers
    timetables = a.generate_timetables(constraints, max_results=200)
    
    if not timetables:
        return jsonify({
            'success': False,
            'error': 'No valid timetables found'
        })
    
    # Group timetables by slot pattern
    patterns = defaultdict(list)
    for tt in timetables:
        pattern_key = get_slot_pattern(tt)
        patterns[pattern_key].append(tt)
    
    # For each slot pattern, aggregate all courses that can go in each slot
    slot_patterns = []
    required_courses_set = set(data.get('courses', []))
    
    for pattern_key, pattern_timetables in patterns.items():
        # Build slot data
        slot_data = defaultdict(lambda: {'courses': [], 'seen': set()})
        
        for tt in pattern_timetables:
            for course in tt:
                course_key = f"{course.short_title}_{course.section}"
                
                # Add course to each slot it occupies
                for day, time, venue in course.get_time_slots():
                    slot_key = get_slot_key(day, time)
                    
                    if course_key not in slot_data[slot_key]['seen']:
                        slot_data[slot_key]['seen'].add(course_key)
                        slot_data[slot_key]['courses'].append({
                            'short_title': course.short_title,
                            'section': course.section,
                            'instructor': course.instructor,
                            'instructor_short': course.instructor_short,
                            'category': course.category,
                            'credit_hours': course.credit_hours,
                            'is_lab': course.is_lab(),
                            'is_required': course.short_title in required_courses_set,
                            'slots': [{'day': d, 'time': t, 'venue': v} for d, t, v in course.get_time_slots()]
                        })
        
        # Convert slot_data to list format
        slots_list = []
        for slot_key in sorted(slot_data.keys()):
            day, time = slot_key.split('_')
            slots_list.append({
                'key': slot_key,
                'day': day,
                'time': time,
                'courses': slot_data[slot_key]['courses']
            })
        
        # Get all unique courses in this pattern
        all_courses_in_pattern = set()
        for tt in pattern_timetables:
            for c in tt:
                all_courses_in_pattern.add(c.short_title)
        
        # Calculate summary
        sample_tt = pattern_timetables[0]
        total_credits = sum(c.credit_hours for c in sample_tt)
        
        slot_patterns.append({
            'pattern_id': len(slot_patterns) + 1,
            'slots': slots_list,
            'slot_keys': sorted(list(pattern_key)),
            'num_courses': len(sample_tt),
            'total_credits': total_credits,
            'num_variations': len(pattern_timetables),
            'summary': ' + '.join(sorted(all_courses_in_pattern)),
            # Include a sample valid selection for initial display
            'default_selection': [{
                'short_title': c.short_title,
                'section': c.section,
                'instructor': c.instructor,
                'instructor_short': c.instructor_short,
                'category': c.category,
                'credit_hours': c.credit_hours,
                'is_lab': c.is_lab(),
                'is_required': c.short_title in required_courses_set,
                'slots': [{'day': d, 'time': t, 'venue': v} for d, t, v in c.get_time_slots()]
            } for c in sample_tt]
        })
    
    # Sort by number of variations (more options = more flexibility)
    slot_patterns.sort(key=lambda p: p['num_variations'], reverse=True)
    
    # No limit - show all patterns
    
    return jsonify({
        'success': True,
        'patterns': slot_patterns,
        'time_slots': a.TIME_SLOTS,
        'days': a.DAYS
    })

@app.route('/api/calendar/subscribe', methods=['POST'])
def subscribe_calendar():
    """Store a timetable selection and return a subscribable calendar URL."""
    data = request.json
    calendar_id = data.get('calendar_id') or secrets.token_urlsafe(8)
    courses = data.get('courses', {})

    # Sanitize ID
    calendar_id = re.sub(r'[^A-Za-z0-9_-]', '', calendar_id)
    if not calendar_id:
        return jsonify({'success': False, 'error': 'Invalid calendar ID'}), 400

    # Persist selection
    cal_path = os.path.join(CALENDARS_DIR, f'{calendar_id}.json')
    with open(cal_path, 'w') as f:
        json.dump(courses, f)

    cal_url = request.host_url.rstrip('/') + f'/calendar/{calendar_id}.ics'

    return jsonify({
        'success': True,
        'calendar_id': calendar_id,
        'calendar_url': cal_url,
    })


@app.route('/calendar/<calendar_id>.ics')
def serve_calendar(calendar_id):
    """Serve a dynamically generated ICS file for calendar subscription."""
    if not re.match(r'^[A-Za-z0-9_-]+$', calendar_id):
        return Response('Invalid calendar ID', status=400)

    cal_path = os.path.join(CALENDARS_DIR, f'{calendar_id}.json')
    if not os.path.exists(cal_path):
        return Response('Calendar not found', status=404)

    with open(cal_path) as f:
        courses = json.load(f)

    # Dynamically update the slots and venues using the latest timetable data
    a = get_analyzer()
    for title, course_data in courses.items():
        section = course_data.get('section')
        if section:
            # Find the matching course in the latest timetable
            matching_course = next((c for c in a.courses if c.short_title == title and c.section == section), None)
            if matching_course:
                course_data['slots'] = [{'day': d, 'time': t, 'venue': v} for d, t, v in matching_course.get_time_slots()]
                course_data['instructor'] = matching_course.instructor
                course_data['is_lab'] = matching_course.is_lab()

    ics_content = _generate_ics(courses)

    return Response(
        ics_content,
        mimetype='text/calendar',
        headers={
            'Content-Disposition': f'inline; filename=timetable.ics',
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0',
        }
    )

def credentials_to_dict(credentials):
    return {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }

@app.route('/login')
def login():
    if not os.path.exists(CLIENT_SECRETS_FILE):
        return jsonify({'success': False, 'error': 'credentials.json not found'}), 400
        
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES)
    flow.redirect_uri = url_for('oauth2callback', _external=True)
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent' # Force consent to get refresh_token
    )
    session['state'] = state
    # Save the code verifier for PKCE
    session['code_verifier'] = flow.code_verifier
    return redirect(authorization_url)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/api/calendar/callback')
def oauth2callback():
    state = session.get('state')
    if not state:
        return "Session state missing", 400
        
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES, state=state)
    flow.redirect_uri = url_for('oauth2callback', _external=True)
    
    if 'code_verifier' in session:
        flow.code_verifier = session.get('code_verifier')
    
    authorization_response = request.url
    flow.fetch_token(authorization_response=authorization_response)
    
    credentials = flow.credentials
    session['credentials'] = credentials_to_dict(credentials)
    
    # Get user info from Google
    user_info_url = "https://www.googleapis.com/oauth2/v1/userinfo"
    params = {'access_token': credentials.token, 'alt': 'json'}
    answer = requests.get(user_info_url, params=params)
    data = answer.json()
    
    google_id = data.get('id')
    email = data.get('email')
    name = data.get('name')
    picture = data.get('picture')
    
    user = User.query.filter_by(google_id=google_id).first()
    if not user:
        user = User(google_id=google_id, email=email, name=name, picture=picture)
        db.session.add(user)
    else:
        user.email = email
        user.name = name
        user.picture = picture
        
    if credentials.refresh_token:
        user.refresh_token = credentials.refresh_token
        
    db.session.commit()
    
    session.permanent = True
    login_user(user, remember=True)
    
    return redirect(url_for('index'))


def sync_user_calendar(user):
    if not user.refresh_token:
        return False
        
    # Get user courses
    courses_to_sync = {c.course_title: {
        'section': c.section,
        'category': c.category,
        'credit_hours': c.credit_hours,
        'is_lab': c.is_lab,
        'instructor': c.instructor,
        'slots': c.get_slots()
    } for c in user.courses}
    
    if not courses_to_sync:
        return True # Nothing to sync
        
    creds_dict = {
        'token': None,
        'refresh_token': user.refresh_token,
        'token_uri': 'https://oauth2.googleapis.com/token',
        'client_id': json.load(open(CLIENT_SECRETS_FILE))['web']['client_id'],
        'client_secret': json.load(open(CLIENT_SECRETS_FILE))['web']['client_secret'],
        'scopes': SCOPES
    }
    
    credentials = google.oauth2.credentials.Credentials(**creds_dict)
    service = build('calendar', 'v3', credentials=credentials)
    
    calendar_id = 'primary'
    
    if user.calendar_type == 'dedicated':
        page_token = None
        found_calendar_id = None
        while True:
            calendar_list = service.calendarList().list(pageToken=page_token).execute()
            for calendar_list_entry in calendar_list['items']:
                if calendar_list_entry['summary'] == 'FAST Timetable':
                    found_calendar_id = calendar_list_entry['id']
                    break
            page_token = calendar_list.get('nextPageToken')
            if not page_token or found_calendar_id:
                break
                
        if found_calendar_id:
            calendar_id = found_calendar_id
        else:
            # Create a new calendar
            new_calendar = {
                'summary': 'FAST Timetable',
                'timeZone': 'Asia/Karachi'
            }
            created_calendar = service.calendars().insert(body=new_calendar).execute()
            calendar_id = created_calendar['id']

    from datetime import timezone
    now = datetime.now(timezone.utc).isoformat()
    
    old_events_result = service.events().list(calendarId=calendar_id, q='[FAST]', maxResults=2500).execute()
    new_events_result = service.events().list(calendarId=calendar_id, privateExtendedProperty='source=fast-timetable', maxResults=2500).execute()
    
    seen_ids = set()
    existing_events = []
    for event in old_events_result.get('items', []) + new_events_result.get('items', []):
        if event['id'] not in seen_ids:
            seen_ids.add(event['id'])
            existing_events.append(event)
    
    existing_by_course = defaultdict(list)
    for event in existing_events:
        summary = event.get('summary', '')
        props = event.get('extendedProperties', {}).get('private', {})
        
        if props.get('source') == 'fast-timetable' and 'course_title' in props:
            existing_by_course[props['course_title']].append(event)
        elif summary.startswith('[FAST] '):
            match = re.match(r'\[FAST\] (.*?) \((.*?)\)', summary)
            if match:
                course_title = match.group(1)
                existing_by_course[course_title].append(event)
    
    a = get_analyzer()
    for title, course_data in courses_to_sync.items():
        section = course_data.get('section')
        if section:
            matching_course = next((c for c in a.courses if c.short_title == title and c.section == section), None)
            if matching_course:
                course_data['slots'] = [{'day': d, 'time': t, 'venue': v} for d, t, v in matching_course.get_time_slots()]
                course_data['instructor'] = matching_course.instructor
                course_data['is_lab'] = matching_course.is_lab()
                course_data['category'] = matching_course.category
                course_data['credit_hours'] = matching_course.credit_hours

    SEMESTER_START = datetime(2026, 8, 17)
    SEMESTER_END = datetime(2026, 12, 4, 23, 59, 59)
    DAY_WEEKDAY = {'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3, 'Fri': 4}
    DAY_ICS = {'Mon': 'MO', 'Tue': 'TU', 'Wed': 'WE', 'Thu': 'TH', 'Fri': 'FR'}
    LECTURE_DUR = timedelta(minutes=80)
    LAB_DUR = timedelta(minutes=170)

    batch = service.new_batch_http_request()
    
    for title, course in courses_to_sync.items():
        if title in existing_by_course:
            for event in existing_by_course[title]:
                batch.add(service.events().delete(calendarId=calendar_id, eventId=event['id']))
            del existing_by_course[title]
            
        is_lab = course.get('is_lab', False)
        dur = LAB_DUR if is_lab else LECTURE_DUR
        section = course.get('section', '')
        instructor = course.get('instructor', 'TBD')
        category = course.get('category', 'N/A')
        credits = course.get('credit_hours', 3)

        by_day = {}
        for slot in course.get('slots', []):
            t = slot['time'].split('-')[0]
            d = slot['day']
            if d not in by_day or t < by_day[d]['time']:
                by_day[d] = {'time': t, 'venue': slot.get('venue', 'TBD')}

        by_time = {}
        for day, info in by_day.items():
            t = info['time']
            if t not in by_time:
                by_time[t] = {'days': [], 'venue': info['venue']}
            by_time[t]['days'].append(day)

        for time_str, group in by_time.items():
            h, m = map(int, time_str.split(':'))
            sorted_days = sorted(group['days'], key=lambda d: DAY_WEEKDAY.get(d, 0))
            
            first = SEMESTER_START
            target_wd = DAY_WEEKDAY[sorted_days[0]]
            while first.weekday() != target_wd:
                first += timedelta(days=1)
            start_dt = first.replace(hour=h, minute=m, second=0)
            end_dt = start_dt + dur
            
            byday = ','.join(DAY_ICS[d] for d in sorted_days)
            location = group['venue'] or 'TBD'
            
            event_body = {
                'summary': f'{title} ({section})',
                'location': location,
                'description': f'Instructor: {instructor}\nSection: {section}\nCategory: {category}\nCredits: {credits}',
                'colorId': user.calendar_color_id or '7',
                'start': {
                    'dateTime': start_dt.isoformat(),
                    'timeZone': 'Asia/Karachi',
                },
                'end': {
                    'dateTime': end_dt.isoformat(),
                    'timeZone': 'Asia/Karachi',
                },
                'recurrence': [
                    f'RRULE:FREQ=WEEKLY;BYDAY={byday};UNTIL={SEMESTER_END.strftime("%Y%m%dT%H%M%SZ")}'
                ],
                'extendedProperties': {
                    'private': {
                        'source': 'fast-timetable',
                        'course_title': title
                    }
                }
            }
            batch.add(service.events().insert(calendarId=calendar_id, body=event_body))

    for title, events in existing_by_course.items():
        for event in events:
            batch.add(service.events().delete(calendarId=calendar_id, eventId=event['id']))
            
    # Execute all insertions and deletions in a single HTTP request!
    batch.execute()
    return True

@app.route('/sync-loading', methods=['POST'])
@login_required
def sync_loading():
    courses_json = request.form.get('courses', '{}')
    return render_template('sync_loading.html', courses_json=courses_json)

@app.route('/sync-success')
@login_required
def sync_success():
    return render_template('sync_success.html')

@app.route('/api/user/courses', methods=['POST'])
@login_required
def save_user_courses():
    data = request.json
    courses_data = data.get('courses', {})
    
    # Delete old courses
    for old_c in current_user.courses:
        db.session.delete(old_c)
    
    # Add new courses
    for title, course_info in courses_data.items():
        new_c = UserCourse(
            user_id=current_user.id,
            course_title=title,
            section=course_info.get('section'),
            category=course_info.get('category'),
            credit_hours=course_info.get('credit_hours'),
            is_lab=course_info.get('is_lab', False),
            instructor=course_info.get('instructor')
        )
        new_c.set_slots(course_info.get('slots', []))
        db.session.add(new_c)
        
    db.session.commit()
    
    # Run sync synchronously so the loading screen can wait for it
    try:
        sync_user_calendar(current_user)
    except Exception as e:
        print(f"Calendar sync failed: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
        
    return jsonify({'success': True})
    
@app.route('/api/user/courses', methods=['GET'])
@login_required
def get_user_courses():
    courses_dict = {}
    for c in current_user.courses:
        courses_dict[c.course_title] = {
            'section': c.section,
            'category': c.category,
            'credit_hours': c.credit_hours,
            'is_lab': c.is_lab,
            'instructor': c.instructor,
            'slots': c.get_slots()
        }
    return jsonify({'success': True, 'courses': courses_dict})

def _generate_ics(courses: dict) -> str:
    """Build an RFC 5545 ICS string from stored course selection."""
    SEMESTER_START = datetime(2026, 8, 17)
    SEMESTER_END = datetime(2026, 12, 4, 23, 59, 59)

    DAY_ICS = {'Mon': 'MO', 'Tue': 'TU', 'Wed': 'WE', 'Thu': 'TH', 'Fri': 'FR'}
    DAY_WEEKDAY = {'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3, 'Fri': 4}

    LECTURE_DUR = timedelta(minutes=80)
    LAB_DUR = timedelta(minutes=170)

    def fmt(dt):
        return dt.strftime('%Y%m%dT%H%M%S')

    events = []

    for title, course in courses.items():
        is_lab = course.get('is_lab', False)
        dur = LAB_DUR if is_lab else LECTURE_DUR

        # Deduplicate: earliest time per day (labs span two consecutive slots)
        by_day = {}
        for slot in course.get('slots', []):
            t = slot['time'].split('-')[0]
            d = slot['day']
            if d not in by_day or t < by_day[d]['time']:
                by_day[d] = {'time': t, 'venue': slot.get('venue', 'TBD')}

        # Group by time so Mon+Wed at same time → single recurring event
        by_time = {}
        for day, info in by_day.items():
            t = info['time']
            if t not in by_time:
                by_time[t] = {'days': [], 'venue': info['venue']}
            by_time[t]['days'].append(day)

        for time_str, group in by_time.items():
            h, m = map(int, time_str.split(':'))
            sorted_days = sorted(group['days'], key=lambda d: DAY_WEEKDAY.get(d, 0))

            # First occurrence of the earliest weekday on or after semester start
            first = SEMESTER_START
            target_wd = DAY_WEEKDAY[sorted_days[0]]
            while first.weekday() != target_wd:
                first += timedelta(days=1)
            start_dt = first.replace(hour=h, minute=m, second=0)
            end_dt = start_dt + dur

            byday = ','.join(DAY_ICS[d] for d in sorted_days)
            uid = str(uuid_module.uuid5(uuid_module.NAMESPACE_URL, f'{title}_{time_str}_{byday}'))

            section = course.get('section', '')
            instructor = course.get('instructor', 'TBD')
            category = course.get('category', 'N/A')
            credits = course.get('credit_hours', 3)
            location = group['venue'] or 'TBD'

            event_lines = [
                'BEGIN:VEVENT',
                f'UID:{uid}',
                f'DTSTAMP:{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}',
                f'DTSTART;TZID=Asia/Karachi:{fmt(start_dt)}',
                f'DTEND;TZID=Asia/Karachi:{fmt(end_dt)}',
                f'RRULE:FREQ=WEEKLY;BYDAY={byday};UNTIL={fmt(SEMESTER_END)}',
                f'SUMMARY:{title} ({section})',
                f'DESCRIPTION:Instructor: {instructor}\\nSection: {section}\\nCategory: {category}\\nCredits: {credits}',
                f'LOCATION:{location}',
                'STATUS:CONFIRMED',
                'END:VEVENT',
            ]
            events.append('\r\n'.join(event_lines))

    vtimezone = '\r\n'.join([
        'BEGIN:VTIMEZONE',
        'TZID:Asia/Karachi',
        'BEGIN:STANDARD',
        'DTSTART:19700101T000000',
        'TZOFFSETFROM:+0500',
        'TZOFFSETTO:+0500',
        'TZNAME:PKT',
        'END:STANDARD',
        'END:VTIMEZONE',
    ])

    return '\r\n'.join([
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//FAST Timetable Generator//EN',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        'X-WR-CALNAME:FAST Fall 2026 Timetable',
        'X-WR-TIMEZONE:Asia/Karachi',
        'REFRESH-INTERVAL;VALUE=DURATION:PT1H',
        'X-PUBLISHED-TTL:PT1H',
        vtimezone,
        '\r\n'.join(events),
        'END:VCALENDAR',
    ])



@app.route('/api/user/preferences', methods=['POST'])
@login_required
def save_user_preferences():
    data = request.json
    if 'calendar_type' in data:
        current_user.calendar_type = data['calendar_type']
    if 'calendar_color_id' in data:
        current_user.calendar_color_id = data['calendar_color_id']
    
    db.session.commit()
    
    # Trigger a background sync with new preferences if they have courses
    try:
        if current_user.courses:
            sync_user_calendar(current_user)
    except Exception as e:
        print(f"Calendar sync failed after preference update: {e}")
        
    return jsonify({'success': True})

@app.route('/api/user/preferences', methods=['GET'])
@login_required
def get_user_preferences():
    return jsonify({
        'success': True,
        'calendar_type': current_user.calendar_type,
        'calendar_color_id': current_user.calendar_color_id
    })

if __name__ == '__main__':
    print("🚀 Starting Timetable Analyzer Web UI...")
    print("📂 Loading timetable data...")
    get_analyzer()  # Pre-load
    print("✅ Ready! Open http://127.0.0.1:5001 in your browser")
    app.run(debug=True, port=5001)
