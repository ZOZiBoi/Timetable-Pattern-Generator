#!/usr/bin/env python3
"""
Timetable Analyzer Web UI - Slot-Based Approach
"""

from flask import Flask, render_template, request, jsonify
from timetable_analyzer import TimetableAnalyzer, TimetableConstraints
from collections import defaultdict

app = Flask(__name__)

# Load analyzer globally
XLSX_PATH = "timetable/FSC Timetable Spring 2026 v1.1.xlsx"
analyzer = None

def get_analyzer():
    global analyzer
    if analyzer is None:
        analyzer = TimetableAnalyzer(XLSX_PATH)
    return analyzer


@app.route('/')
def index():
    return render_template('index.html')


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
        excluded_time_slots=data.get('excluded_slots', []),
        wildcard_counts=wildcard_counts,
        section_preferences=section_preferences,
    )
    
    a = get_analyzer()
    
    # Generate many timetables to find diverse slot patterns
    # Increase to 1000 to capture more lab/section combinations
    timetables = a.generate_timetables(constraints, max_results=1000)
    
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


if __name__ == '__main__':
    print("🚀 Starting Timetable Analyzer Web UI...")
    print("📂 Loading timetable data...")
    get_analyzer()  # Pre-load
    print("✅ Ready! Open http://127.0.0.1:5000 in your browser")
    app.run(debug=True, port=5000)
