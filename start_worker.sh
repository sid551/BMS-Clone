#!/bin/bash
# Celery worker startup script for Render
cd /opt/render/project/src/djnago-bookmyshow-clone
celery -A bookmyseat worker --loglevel=info --concurrency=2 -Q celery
