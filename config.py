#!/usr/bin/env python3
"""
Configuration file - Single source of truth for timetable filename.
Change the TIMETABLE_FILENAME here to update it across the entire application.
"""

# Timetable filename - change this to update the timetable file used across the app
TIMETABLE_FILENAME = "timetable/FSC_F26_TT_v1.0.9_23082026.xlsx"

import os

# Database configuration: Use DATABASE_URL environment variable if provided (e.g. on Render)
# Otherwise, fall back to local SQLite database.
# SQLAlchemy 1.4+ requires postgresql:// instead of postgres://
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///app.db')
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

SQLALCHEMY_DATABASE_URI = DATABASE_URL
SQLALCHEMY_TRACK_MODIFICATIONS = False
