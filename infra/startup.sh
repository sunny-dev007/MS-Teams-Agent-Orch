#!/bin/bash
cd /app
uvicorn agent.main:app --host 0.0.0.0 --port 8000
