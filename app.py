#!/usr/bin/env python3
"""
Timetable Analyzer Web UI - Slot-Based Approach
"""

from flask import Flask, render_template, request, jsonify, Response
import os
import re
import json
import secrets
import uuid as uuid_module
from datetime import datetime, timedelta
from timetable_analyzer import TimetableAnalyzer, TimetableConstraints
from collections import defaultdict
from config import TIMETABLE_FILENAME

app = Flask(__name__)

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
                f'DTSTAMP:{datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")}',
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


if __name__ == '__main__':
    print("🚀 Starting Timetable Analyzer Web UI...")
    print("📂 Loading timetable data...")
    get_analyzer()  # Pre-load
    print("✅ Ready! Open http://127.0.0.1:5001 in your browser")
    app.run(debug=True, port=5001)
