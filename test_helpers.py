"""Shared TestClient wired to a throwaway database.

This module MUST be imported first, before `auth` or `history`: db.DB_PATH is
captured when db is first imported, so the POTATO_DB override below only takes
effect if nothing has touched db yet. Importing it also creates the schema, so
tests never depend on the developer's real potatodoc.db.
"""
import os
import tempfile

os.environ["POTATO_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="potatodoc-test-"), "test.db"
)

from db import init_db

init_db()

from fastapi import FastAPI
from starlette.testclient import TestClient

from auth import router as auth_router
from history import router as history_router
from notices import router as notices_router
from tickets import router as tickets_router
from admin import router as admin_router
from location import router as location_router

app = FastAPI()
app.include_router(auth_router)
app.include_router(history_router)
app.include_router(notices_router)
app.include_router(tickets_router)
app.include_router(admin_router)
app.include_router(location_router)

client = TestClient(app)
