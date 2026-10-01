"""Shared TestClient wired to a throwaway database.

Import this FIRST from every test module: db.DB_PATH is read at import time.
The history router is mounted later, in the history task.
"""
import os
import tempfile

os.environ["POTATO_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="potatodoc-test-"), "test.db"
)

from fastapi import FastAPI
from starlette.testclient import TestClient

from auth import router as auth_router

app = FastAPI()
app.include_router(auth_router)

client = TestClient(app)
