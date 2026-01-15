#!/usr/bin/env python3
"""
Timetable Analyzer - FAST School of Computing
Analyzes xlsx timetable and generates personalized schedules with constraints.
"""

import pandas as pd
import re
from dataclasses import dataclass, field
from typing import Optional
from itertools import combinations, product


@dataclass
class Course:
    """Represents a single course offering."""
    code: str
    title: str
    short_title: str
    section: str
    instructor: str
    instructor_short: str
    credit_hours: int
    category: str
    day1: Optional[str] = None
    slot1: Optional[str] = None
    venue1: Optional[str] = None
    day2: Optional[str] = None
    slot2: Optional[str] = None
    venue2: Optional[str] = None
    duration_minutes: int = 80  # Default 80 mins, labs are ~170 mins (2 slots)
    
    # Time slot order for conflict detection
    TIME_SLOT_ORDER = ["08:30", "10:00", "11:30", "13:00", "14:30", "16:00", "17:30", "19:00"]
    
    def is_lab(self) -> bool:
        """Check if this is a lab course (takes 2 time slots)."""
        return self.duration_minutes > 100 or "Lab" in self.title
    
    def get_time_slots(self) -> list[tuple[str, str, str]]:
        """Returns list of (day, slot, venue) tuples for this course.
        For labs, includes the consecutive slot as well."""
        slots = []
        
        if self.day1 and self.slot1:
            slots.append((self.day1, self.slot1, self.venue1 or "TBD"))
            # If it's a lab, add the next time slot too
            if self.is_lab():
                next_slot = self._get_next_slot(self.slot1)
                if next_slot:
                    slots.append((self.day1, next_slot, self.venue1 or "TBD"))
        
        if self.day2 and self.slot2:
            slots.append((self.day2, self.slot2, self.venue2 or "TBD"))
            # If it's a lab, add the next time slot too
            if self.is_lab():
                next_slot = self._get_next_slot(self.slot2)
                if next_slot:
                    slots.append((self.day2, next_slot, self.venue2 or "TBD"))
        
        return slots
    
    def _get_next_slot(self, slot: str) -> Optional[str]:
        """Get the next consecutive time slot."""
        try:
            idx = self.TIME_SLOT_ORDER.index(slot)
            if idx + 1 < len(self.TIME_SLOT_ORDER):
                return self.TIME_SLOT_ORDER[idx + 1]
        except ValueError:
            pass
        return None
    
    def conflicts_with(self, other: 'Course') -> bool:
        """Check if this course conflicts with another course."""
        my_slots = self.get_time_slots()
        other_slots = other.get_time_slots()
        
        for (day1, slot1, _) in my_slots:
            for (day2, slot2, _) in other_slots:
                if day1 == day2 and slot1 == slot2:
                    return True
        return False
    
    def __str__(self):
        slots = self.get_time_slots()
        slot_str = ", ".join([f"{d} {s}" for d, s, _ in slots])
        lab_marker = " [LAB]" if self.is_lab() else ""
        return f"{self.short_title} ({self.section}): {self.instructor_short} [{slot_str}]{lab_marker}"


@dataclass  
class TimetableConstraints:
    """Constraints for generating timetable."""
    excluded_instructors: list[str] = field(default_factory=list)
    excluded_time_slots: list[str] = field(default_factory=list)  # e.g., ["08:30", "19:00"]
    required_courses: list[str] = field(default_factory=list)  # Course short titles
    wildcard_counts: dict[str, int] = field(default_factory=dict)  # e.g., {"CS Elective": 2, "University Elective": 1}
    section_preferences: dict[str, str] = field(default_factory=dict)  # e.g., {"AI Lab": "BCS-4A"} or {"AI": "any"}
    batch: str = "BCS-2022"
    
    def get_semester_prefix(self) -> str:
        """Get the semester prefix based on batch year."""
        batch_to_semester = {
            "BCS-2025": "BCS-2",
            "BCS-2024": "BCS-4", 
            "BCS-2023": "BCS-6",
            "BCS-2022": "BCS-8",
            "BCS-2021": "BCS-10",
        }
        return batch_to_semester.get(self.batch, "BCS-8")


class TimetableAnalyzer:
    """Main analyzer class for timetable generation."""
    
    TIME_SLOTS = [
        "08:30", "10:00", "11:30", "13:00", 
        "14:30", "16:00", "17:30", "19:00"
    ]
    
    DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    
    # Simplified wildcard categories
    WILDCARD_CATEGORIES = {
        "cs_elective": "CS Elective",
        "university_elective": "University Elective",
        "robo_elective": "Robo Elective",
    }
    
    # Map simplified categories to actual categories in the spreadsheet
    UNIVERSITY_ELECTIVE_CATEGORIES = [
        "MG (Elective)", 
        "HSS (Elective)",
        "Mandatory Elec",
    ]
    
    ROBO_ELECTIVE_CATEGORY = "Robo (Elective)"
    
    CS_ELECTIVE_CATEGORY = "CS (Elective)"
    
    def __init__(self, xlsx_path: str):
        self.xlsx_path = xlsx_path
        self.courses: list[Course] = []
        self._load_data()
    
    def _load_data(self):
        """Load and parse the timetable data from xlsx."""
        # Read CS sheet
        df = pd.read_excel(self.xlsx_path, sheet_name='CS', header=None)
        df.columns = df.iloc[1]
        df = df.iloc[2:].reset_index(drop=True)
        
        # Parse courses
        for _, row in df.iterrows():
            if pd.isna(row['Section']) or pd.isna(row['Course Title']):
                continue
                
            # Parse duration (labs are typically 170 mins = 2 slots)
            duration = 80  # default
            if pd.notna(row.get('Duration in Minutes')):
                try:
                    duration = int(float(row['Duration in Minutes']))
                except (ValueError, TypeError):
                    duration = 80
            
            course = Course(
                code=str(row['Code']) if pd.notna(row['Code']) else "",
                title=str(row['Course Title']) if pd.notna(row['Course Title']) else "",
                short_title=str(row['Course Short Title']) if pd.notna(row['Course Short Title']) else "",
                section=str(row['Section']) if pd.notna(row['Section']) else "",
                instructor=str(row['Instructor Name']) if pd.notna(row['Instructor Name']) else "TBD",
                instructor_short=str(row['Instructor Short Name']) if pd.notna(row['Instructor Short Name']) else "TBD",
                credit_hours=int(row['Credit Hours']) if pd.notna(row['Credit Hours']) and str(row['Credit Hours']).isdigit() else 3,
                category=str(row['Category ']).strip() if pd.notna(row['Category ']) else "",
                day1=str(row['Day 1']) if pd.notna(row['Day 1']) else None,
                slot1=str(row['Slot 1']) if pd.notna(row['Slot 1']) else None,
                venue1=str(row['Venue 1']) if pd.notna(row['Venue 1']) else None,
                day2=str(row['Day 2']) if pd.notna(row['Day 2']) else None,
                slot2=str(row['Slot 2']) if pd.notna(row['Slot 2']) else None,
                venue2=str(row['Venue 2']) if pd.notna(row['Venue 2']) else None,
                duration_minutes=duration,
            )
            self.courses.append(course)
        
        print(f"✓ Loaded {len(self.courses)} course offerings")
    
    def get_courses_for_batch(self, batch: str) -> list[Course]:
        """Get all courses available for a specific batch."""
        prefix = TimetableConstraints(batch=batch).get_semester_prefix()
        return [c for c in self.courses if c.section.startswith(prefix)]
    
    def get_unique_courses(self, courses: list[Course]) -> dict[str, list[Course]]:
        """Group courses by their short title (different sections of same course)."""
        grouped = {}
        for course in courses:
            key = course.short_title
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(course)
        return grouped
    
    def get_available_instructors(self, batch: str = "BCS-2022") -> list[str]:
        """Get list of all instructors for the batch."""
        courses = self.get_courses_for_batch(batch)
        instructors = set()
        for c in courses:
            if c.instructor and c.instructor != "TBD":
                instructors.add(c.instructor)
        return sorted(list(instructors))
    
    def get_all_cs_instructors(self) -> list[str]:
        """Get list of ALL CS instructors (all batches)."""
        instructors = set()
        for c in self.courses:
            # Only BCS sections
            if c.section.startswith("BCS-") and c.instructor and c.instructor != "TBD":
                instructors.add(c.instructor)
        return sorted(list(instructors))
    
    def get_available_courses(self, batch: str = "BCS-2022") -> dict[str, list[str]]:
        """Get available courses grouped by category."""
        courses = self.get_courses_for_batch(batch)
        by_category = {}
        
        for course in courses:
            cat = course.category or "Uncategorized"
            if cat not in by_category:
                by_category[cat] = set()
            by_category[cat].add(course.short_title)
        
        return {k: sorted(list(v)) for k, v in by_category.items()}
    
    def get_all_cs_courses(self) -> dict[str, list[str]]:
        """Get ALL CS courses grouped by category (all batches, for repeaters)."""
        by_category = {}
        
        for course in self.courses:
            # Only BCS sections
            if not course.section.startswith("BCS-"):
                continue
            
            cat = course.category or "Uncategorized"
            if cat not in by_category:
                by_category[cat] = set()
            by_category[cat].add(course.short_title)
        
        return {k: sorted(list(v)) for k, v in by_category.items()}
    
    def filter_courses(self, constraints: TimetableConstraints, include_all_for_required: bool = True) -> list[Course]:
        """Filter courses based on constraints.
        
        Args:
            constraints: The filtering constraints
            include_all_for_required: If True, includes courses from ALL semesters for required courses (for repeaters)
        """
        prefix = constraints.get_semester_prefix()
        filtered = []
        
        for course in self.courses:
            # Only consider BCS courses
            if not course.section.startswith("BCS-"):
                continue
            
            # Check if this is a required course (allow from any semester for repeaters)
            is_required = course.short_title in constraints.required_courses
            
            # For non-required courses (wildcards), filter by batch
            if not is_required and not course.section.startswith(prefix):
                continue
            
            # Check excluded instructors
            if constraints.excluded_instructors:
                instructor_excluded = False
                for exc in constraints.excluded_instructors:
                    if exc.lower() in course.instructor.lower() or exc.lower() in course.instructor_short.lower():
                        instructor_excluded = True
                        break
                if instructor_excluded:
                    continue
            
            # Check excluded time slots
            if constraints.excluded_time_slots:
                slot_excluded = False
                for slot in course.get_time_slots():
                    for exc_slot in constraints.excluded_time_slots:
                        if slot[1] and slot[1].startswith(exc_slot):
                            slot_excluded = True
                            break
                    if slot_excluded:
                        break
                if slot_excluded:
                    continue
            
            filtered.append(course)
        
        return filtered
    
    def _matches_wildcard(self, course_category: str, wildcard: str) -> bool:
        """Check if a course category matches a wildcard category."""
        if wildcard == "University Elective":
            return course_category in self.UNIVERSITY_ELECTIVE_CATEGORIES
        elif wildcard == "CS Elective":
            return course_category == self.CS_ELECTIVE_CATEGORY
        elif wildcard == "Robo Elective":
            return course_category == self.ROBO_ELECTIVE_CATEGORY
        else:
            # Direct match for legacy support
            return course_category == wildcard
    
    def generate_timetables(self, constraints: TimetableConstraints, max_results: int = 10) -> list[list[Course]]:
        """Generate valid timetables based on constraints.
        
        This generates ALL valid section combinations, not just unique course combinations.
        This is important for labs and courses with multiple sections at different times.
        """
        filtered = self.filter_courses(constraints)
        grouped = self.get_unique_courses(filtered)
        
        # Separate required courses and wildcards
        required_options = []
        wildcard_pools = {cat: [] for cat in constraints.wildcard_counts.keys()}
        
        for course_name in constraints.required_courses:
            if course_name in grouped:
                course_sections = grouped[course_name]
                
                # Check if there's a section preference
                section_pref = constraints.section_preferences.get(course_name, 'any')
                
                if section_pref and section_pref != 'any':
                    # section_pref can be a string (single section) or list (multiple sections)
                    if isinstance(section_pref, list):
                        # Filter to only the specified sections
                        filtered_sections = [c for c in course_sections if c.section in section_pref]
                    else:
                        # Single section (legacy format)
                        filtered_sections = [c for c in course_sections if c.section == section_pref]
                    
                    if filtered_sections:
                        required_options.append(filtered_sections)
                    else:
                        print(f"⚠ Warning: Section(s) '{section_pref}' not found for course '{course_name}'")
                        # Fall back to all sections
                        required_options.append(course_sections)
                else:
                    # Use all sections
                    required_options.append(course_sections)
            else:
                print(f"⚠ Warning: Course '{course_name}' not found in available courses")
        
        # Add wildcard courses - match against simplified categories
        for course in filtered:
            if course.short_title not in constraints.required_courses:
                for wildcard_cat in constraints.wildcard_counts.keys():
                    if self._matches_wildcard(course.category, wildcard_cat):
                        if wildcard_cat not in wildcard_pools:
                            wildcard_pools[wildcard_cat] = []
                        wildcard_pools[wildcard_cat].append(course)
                        break
        
        # Group wildcards by course name (unique courses with their sections)
        wildcard_by_category = {}
        wildcard_sections_by_name = {}  # Store all sections for each wildcard course
        for cat, courses in wildcard_pools.items():
            cat_grouped = self.get_unique_courses(courses)
            wildcard_by_category[cat] = list(cat_grouped.keys())
            for name, sections in cat_grouped.items():
                wildcard_sections_by_name[name] = sections
        
        valid_timetables = []
        seen_slot_patterns = set()
        
        # Generate combinations for required courses
        if not required_options:
            print("⚠ No required courses specified or found")
            return []
        
        # Generate all possible wildcard course name combinations
        wildcard_name_combos = self._generate_wildcard_combos(constraints.wildcard_counts, wildcard_by_category)
        
        # For each required course section combo + each wildcard combo
        for req_combo in product(*required_options):
            req_list = list(req_combo)
            
            # Check for conflicts within required courses
            if self._has_conflicts(req_list):
                continue
            
            for wildcard_names in wildcard_name_combos:
                # Generate ALL valid section combinations for wildcards (not just the first one)
                wildcard_section_options = []
                for wc_name in wildcard_names:
                    sections = wildcard_sections_by_name.get(wc_name, [])
                    if sections:
                        wildcard_section_options.append(sections)
                
                # If no wildcards, just use the required combo
                if not wildcard_section_options:
                    slot_pattern = self._get_slot_pattern(req_list)
                    if slot_pattern not in seen_slot_patterns:
                        seen_slot_patterns.add(slot_pattern)
                        valid_timetables.append(req_list)
                        if len(valid_timetables) >= max_results:
                            return valid_timetables
                    continue
                
                # Generate all combinations of wildcard sections
                for wc_section_combo in product(*wildcard_section_options):
                    full_combo = req_list + list(wc_section_combo)
                    
                    if not self._has_conflicts(full_combo):
                        # Create unique key based on SLOT PATTERN (not just course names)
                        # This allows same courses with different time slots to be separate entries
                        slot_pattern = self._get_slot_pattern(full_combo)
                        
                        if slot_pattern not in seen_slot_patterns:
                            seen_slot_patterns.add(slot_pattern)
                            valid_timetables.append(full_combo)
                            
                            if len(valid_timetables) >= max_results:
                                return valid_timetables
        
        return valid_timetables
    
    def _get_slot_pattern(self, courses: list[Course]) -> tuple:
        """Get a unique slot pattern identifier for a list of courses."""
        slots = []
        for course in courses:
            for day, time, _ in course.get_time_slots():
                slots.append(f"{day}_{time}")
        return tuple(sorted(slots))
    
    def _generate_wildcard_combos(self, wildcard_counts: dict[str, int], 
                                   wildcard_by_category: dict[str, list]) -> list[list[str]]:
        """Generate all possible combinations of wildcard course names."""
        from itertools import combinations as iter_combinations
        
        all_combos = [[]]  # Start with empty combo
        
        for cat, count in wildcard_counts.items():
            if cat not in wildcard_by_category or count == 0:
                continue
            
            available = wildcard_by_category[cat]
            # Get all combinations of 'count' courses from this category
            cat_combos = list(iter_combinations(available, min(count, len(available))))
            
            # Combine with existing combos
            new_all = []
            for existing in all_combos:
                for cat_combo in cat_combos:
                    new_all.append(existing + list(cat_combo))
            all_combos = new_all
        
        return all_combos
    
    def _build_combo_with_wildcards(self, required: list[Course], wildcard_names: list[str],
                                     grouped: dict, filtered: list[Course]) -> list[Course]:
        """Build a full combo by adding wildcard courses that don't conflict."""
        combo = required.copy()
        
        for wc_name in wildcard_names:
            # Find sections for this wildcard course
            sections = [c for c in filtered if c.short_title == wc_name]
            
            # Find a section that doesn't conflict
            added = False
            for section in sections:
                test_combo = combo + [section]
                if not self._has_conflicts(test_combo):
                    combo.append(section)
                    added = True
                    break
            
            if not added:
                return None  # Can't add this wildcard without conflict
        
        return combo
    
    def _has_conflicts(self, courses: list[Course]) -> bool:
        """Check if a list of courses has any time conflicts."""
        for i, c1 in enumerate(courses):
            for c2 in courses[i+1:]:
                if c1.conflicts_with(c2):
                    return True
        return False
    
    def format_timetable(self, courses: list[Course], option_num: int = 1) -> str:
        """Format a timetable for display."""
        # Create a grid
        grid = {day: {slot: [] for slot in self.TIME_SLOTS} for day in self.DAYS}
        
        for course in courses:
            for day, slot, venue in course.get_time_slots():
                if day in grid and slot:
                    # Extract just the start time for matching
                    slot_key = slot.split("-")[0] if "-" in slot else slot
                    if slot_key in grid[day]:
                        grid[day][slot_key].append(f"{course.short_title}")
        
        # Build output
        output = []
        col_width = 18
        total_width = 14 + (col_width * 5)
        
        output.append("")
        output.append("╔" + "═" * (total_width - 2) + "╗")
        title = f"  TIMETABLE OPTION {option_num}  "
        padding = (total_width - 2 - len(title)) // 2
        output.append("║" + " " * padding + title + " " * (total_width - 2 - padding - len(title)) + "║")
        output.append("╠" + "═" * (total_width - 2) + "╣")
        
        # Header row
        header = f"║ {'Time':<12}│"
        for day in self.DAYS:
            header += f" {day:<{col_width-2}} │"
        output.append(header[:-1] + "║")
        output.append("╠" + "═" * 13 + "╪" + ("═" * (col_width) + "╪") * 4 + "═" * (col_width) + "╣")
        
        for slot in self.TIME_SLOTS:
            row = f"║ {slot:<12}│"
            for day in self.DAYS:
                cell = ", ".join(grid[day][slot]) if grid[day][slot] else "·"
                # Truncate if too long
                if len(cell) > col_width - 2:
                    cell = cell[:col_width-4] + ".."
                row += f" {cell:<{col_width-2}} │"
            output.append(row[:-1] + "║")
        
        output.append("╚" + "═" * (total_width - 2) + "╝")
        
        # Course details in a nice table
        output.append("")
        output.append("┌" + "─" * (total_width - 2) + "┐")
        output.append("│" + " COURSE DETAILS".ljust(total_width - 2) + "│")
        output.append("├" + "─" * (total_width - 2) + "┤")
        
        for course in courses:
            slots = course.get_time_slots()
            schedule = " & ".join([f"{d} {s}" for d, s, _ in slots])
            venues = ", ".join(set([v for _, _, v in slots]))
            
            line1 = f"  ▸ {course.short_title} ({course.section}) - {course.instructor_short}"
            output.append("│" + line1.ljust(total_width - 2) + "│")
            
            line2 = f"    {schedule} @ {venues}"
            output.append("│" + line2.ljust(total_width - 2) + "│")
            
            line3 = f"    [{course.category}]"
            output.append("│" + line3.ljust(total_width - 2) + "│")
            output.append("│" + " " * (total_width - 2) + "│")
        
        output.append("└" + "─" * (total_width - 2) + "┘")
        
        # Summary
        total_credits = sum(c.credit_hours for c in courses)
        output.append("")
        output.append(f"  📊 Total: {len(courses)} courses, {total_credits} credit hours")
        
        return "\n".join(output)


def interactive_mode(analyzer: TimetableAnalyzer):
    """Run the analyzer in interactive mode."""
    print("\n" + "="*60)
    print("  FAST TIMETABLE ANALYZER - Interactive Mode")
    print("="*60)
    
    # Select batch
    print("\n📚 Available Batches:")
    batches = ["BCS-2025", "BCS-2024", "BCS-2023", "BCS-2022", "BCS-2021"]
    for i, b in enumerate(batches, 1):
        print(f"  {i}. {b}")
    
    batch_choice = input("\nSelect batch [default: 4 for BCS-2022]: ").strip()
    batch = batches[int(batch_choice)-1] if batch_choice.isdigit() and 1 <= int(batch_choice) <= len(batches) else "BCS-2022"
    print(f"✓ Selected batch: {batch}")
    
    # Show available courses
    available = analyzer.get_available_courses(batch)
    print(f"\n📖 Available Courses for {batch}:")
    all_courses = []
    for cat, courses in available.items():
        print(f"\n  {cat}:")
        for c in courses:
            all_courses.append(c)
            print(f"    • {c}")
    
    # Select required courses
    print("\n" + "-"*60)
    print("Enter the courses you want to take (comma-separated short titles)")
    print("Example: Web Pro, Cyber Tools, Applied ML, Entrepreneur")
    required_input = input("Required courses: ").strip()
    required_courses = [c.strip() for c in required_input.split(",") if c.strip()]
    
    # Select wildcard categories with counts
    print("\n📌 Wildcard Electives:")
    print("  You can add electives without specifying exact courses.")
    print("  The system will auto-pick non-conflicting electives for you.\n")
    
    wildcard_counts = {}
    
    # CS Electives
    print("  CS Electives: Data Mining, Blockchain, IoT, Web Pro, etc.")
    cs_count = input("  How many CS Electives? [0]: ").strip()
    if cs_count.isdigit() and int(cs_count) > 0:
        wildcard_counts["CS Elective"] = int(cs_count)
    
    # University Electives  
    print("\n  University Electives: Any MG, HSS, or Robo elective")
    uni_count = input("  How many University Electives? [0]: ").strip()
    if uni_count.isdigit() and int(uni_count) > 0:
        wildcard_counts["University Elective"] = int(uni_count)
    
    if wildcard_counts:
        print(f"\n✓ Wildcards: {', '.join([f'{v}x {k}' for k, v in wildcard_counts.items()])}")
    
    # Exclude instructors
    print("\n👤 Instructors for this batch:")
    instructors = analyzer.get_available_instructors(batch)
    for i, inst in enumerate(instructors, 1):
        print(f"  {i}. {inst}")
    
    print("\nEnter instructor numbers to EXCLUDE (comma-separated), or press Enter to skip:")
    exclude_inst_input = input("Exclude instructors: ").strip()
    excluded_instructors = []
    if exclude_inst_input:
        for num in exclude_inst_input.split(","):
            if num.strip().isdigit():
                idx = int(num.strip()) - 1
                if 0 <= idx < len(instructors):
                    excluded_instructors.append(instructors[idx])
    
    # Exclude time slots
    print("\n⏰ Time Slots:")
    for i, slot in enumerate(TimetableAnalyzer.TIME_SLOTS, 1):
        print(f"  {i}. {slot}")
    
    print("\nEnter slot numbers to EXCLUDE (comma-separated), or press Enter to skip:")
    exclude_slot_input = input("Exclude time slots: ").strip()
    excluded_slots = []
    if exclude_slot_input:
        for num in exclude_slot_input.split(","):
            if num.strip().isdigit():
                idx = int(num.strip()) - 1
                if 0 <= idx < len(TimetableAnalyzer.TIME_SLOTS):
                    excluded_slots.append(TimetableAnalyzer.TIME_SLOTS[idx])
    
    # Create constraints
    constraints = TimetableConstraints(
        batch=batch,
        required_courses=required_courses,
        wildcard_counts=wildcard_counts,
        excluded_instructors=excluded_instructors,
        excluded_time_slots=excluded_slots,
    )
    
    # Generate timetables
    print("\n🔄 Generating timetables...")
    timetables = analyzer.generate_timetables(constraints, max_results=5)
    
    if not timetables:
        print("\n❌ No valid timetables found with these constraints!")
        print("Try relaxing some constraints (fewer exclusions, different courses)")
        return
    
    print(f"\n✅ Found {len(timetables)} valid timetable(s)!")
    
    for i, tt in enumerate(timetables, 1):
        print(analyzer.format_timetable(tt, i))
        
        if i < len(timetables):
            cont = input("\nShow next option? [Y/n]: ").strip().lower()
            if cont == 'n':
                break


def main():
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(
        description="FAST Timetable Analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode
  %(prog)s -i

  # 4 specific courses, no 8:30 classes
  %(prog)s --courses "Web Pro" "Applied ML" "Cyber Tools" "Entrepreneur" --exclude-slots 08:30

  # 2 specific courses + 2 CS electives (auto-picked)
  %(prog)s --courses "Web Pro" "Entrepreneur" --cs-electives 2

  # 1 course + 2 university electives + 1 CS elective
  %(prog)s --courses "Web Pro" --university-electives 2 --cs-electives 1
        """
    )
    parser.add_argument("xlsx_file", nargs="?", default="timetable/FSC Timetable Spring 2026 v1.1.xlsx",
                        help="Path to the timetable xlsx file")
    parser.add_argument("--batch", default="BCS-2022", help="Batch year (e.g., BCS-2022)")
    parser.add_argument("--courses", nargs="+", help="Required courses (short titles)")
    parser.add_argument("--exclude-instructors", nargs="+", help="Instructors to exclude")
    parser.add_argument("--exclude-slots", nargs="+", help="Time slots to exclude (e.g., 08:30 19:00)")
    parser.add_argument("--cs-electives", type=int, default=0, help="Number of CS electives to auto-add")
    parser.add_argument("--university-electives", type=int, default=0, help="Number of university electives to auto-add")
    parser.add_argument("--interactive", "-i", action="store_true", help="Run in interactive mode")
    parser.add_argument("--list-courses", action="store_true", help="List available courses")
    parser.add_argument("--list-instructors", action="store_true", help="List available instructors")
    
    args = parser.parse_args()
    
    # Load analyzer
    print(f"📂 Loading timetable from: {args.xlsx_file}")
    analyzer = TimetableAnalyzer(args.xlsx_file)
    
    if args.list_courses:
        print(f"\n📖 Available Courses for {args.batch}:")
        available = analyzer.get_available_courses(args.batch)
        for cat, courses in available.items():
            print(f"\n  {cat}:")
            for c in courses:
                print(f"    • {c}")
        return
    
    if args.list_instructors:
        print(f"\n👤 Instructors for {args.batch}:")
        for inst in analyzer.get_available_instructors(args.batch):
            print(f"  • {inst}")
        return
    
    if args.interactive or not args.courses:
        interactive_mode(analyzer)
        return
    
    # Command-line mode - build wildcard counts
    wildcard_counts = {}
    if args.cs_electives > 0:
        wildcard_counts["CS Elective"] = args.cs_electives
    if args.university_electives > 0:
        wildcard_counts["University Elective"] = args.university_electives
    
    constraints = TimetableConstraints(
        batch=args.batch,
        required_courses=args.courses or [],
        excluded_instructors=args.exclude_instructors or [],
        excluded_time_slots=args.exclude_slots or [],
        wildcard_counts=wildcard_counts,
    )
    
    print("\n🔄 Generating timetables...")
    timetables = analyzer.generate_timetables(constraints, max_results=5)
    
    if not timetables:
        print("\n❌ No valid timetables found!")
        return
    
    print(f"\n✅ Found {len(timetables)} valid timetable(s)!")
    for i, tt in enumerate(timetables, 1):
        print(analyzer.format_timetable(tt, i))


if __name__ == "__main__":
    main()

